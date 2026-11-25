"""Content domain: /api/images, /api/metadata, /api/projects/{address}, /api/comments, /api/agents*,
/api/v1/bugs/reports. Every write here takes an API key or a SIWE session (a client with the signer's key sends the key
alone: the server lets it decide; see the auth modes in ``basis/api/core.py``), except ``register_agent_signed`` (no
auth: a signed message). TypeScript SDK: ``src/api/content.ts``."""

import io
import os
import re
import time
from typing import Any, Dict, List, Optional, Union

import requests

from ..core.errors import BasisError, ValidationError
from ..core.units import to_uint
from .core import ApiCore, BasisApiError, request_with_deadline, seg
from .types import Pagination

try:
    from typing import Literal, TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import Literal, TypedDict

ImagePurpose = Literal["token", "avatar"]
"""``purpose`` of an image upload: ``token`` (needs the token/market address, caller must be its creator) or
``avatar`` (5 a month)."""

BugSeverity = Literal["critical", "high", "medium", "low"]
BugCategory = Literal["sdk", "contracts", "api", "frontend", "docs"]
BugStatus = Literal["pending", "verified", "duplicate", "invalid"]

ImageSource = Union[str, os.PathLike, bytes, bytearray, memoryview]
"""A local file path (``str`` or ``os.PathLike``), or the image bytes."""


class MetadataResult(TypedDict):
    """``POST /api/metadata`` answer: the pinned metadata JSON."""

    url: str
    cid: str


class ProjectUpdateResult(TypedDict):
    """``POST /api/projects/{address}`` answer: ``project`` is the stored project row."""

    success: bool
    project: Dict[str, Any]


class _CommentRowBase(TypedDict):
    id: int
    projectId: int
    author: str
    content: str
    isHidden: bool
    tradeType: Optional[str]
    txHash: Optional[str]
    createdAt: str
    updatedAt: str


class CommentRow(_CommentRowBase, total=False):
    """A stored comment row (``POST /api/comments`` answer). Times are ISO strings; ``author`` is lower-case;
    ``authorName`` the author's username (None when the wallet has none)."""

    authorName: Optional[str]


class _DeleteCommentResultBase(TypedDict):
    ok: bool


class DeleteCommentResult(_DeleteCommentResultBase, total=False):
    """``DELETE /api/comments`` answer (``ok`` is always True)."""

    message: str


class Agent(TypedDict):
    """A registered ERC-8004 agent (spec ``Agent``). ``wallet`` lower-case; ``name`` None only on old rows."""

    wallet: str
    agentId: int
    name: Optional[str]
    description: Optional[str]
    createdAt: str


class AgentList(TypedDict):
    """``GET /api/agents`` answer (page-based: ``pagination.page`` / ``total``)."""

    data: List[Agent]
    pagination: Pagination


class AgentLookup(TypedDict):
    """``GET /api/agents/{address}`` answer."""

    isAgent: bool
    agent: Optional[Agent]


class AgentAppliedUsername(TypedDict, total=False):
    """The registration name as username: ``reason`` says why not (``no_name``, ``unusable``, ``reserved``,
    ``taken``, ``chosen_name_kept``, ...)."""

    applied: bool
    username: str
    reason: str


class AgentAppliedPhoto(TypedDict, total=False):
    """The registration image as the avatar: ``reason`` says why not (``no_image``, ``chosen_photo_kept``,
    ``unreadable``, ...)."""

    applied: bool
    reason: str


class AgentApplied(TypedDict):
    """What an agent sync applied to the wallet's profile from its registration file (``AgentRegistration.applied``)."""

    username: AgentAppliedUsername
    photo: AgentAppliedPhoto


class _AgentRegistrationBase(TypedDict):
    success: bool
    agent: Agent


class AgentRegistration(_AgentRegistrationBase, total=False):
    """``POST /api/agents`` answer (201). ``applied`` (the paper site): what the sync applied to the profile (the
    username and photo, never over ones the wallet chose)."""

    applied: AgentApplied


class BugReportSummary(TypedDict):
    id: int
    title: str
    severity: BugSeverity
    category: BugCategory
    status: BugStatus
    createdAt: str


class BugReportSubmission(TypedDict):
    """``POST /api/v1/bugs/reports`` answer (201)."""

    success: bool
    report: BugReportSummary


class BugReport(TypedDict):
    """One of your bug reports. Times are ISO strings."""

    id: int
    wallet: str
    title: str
    description: str
    severity: BugSeverity
    category: BugCategory
    evidence: Optional[str]
    status: BugStatus
    adminNote: Optional[str]
    createdAt: str
    verifiedAt: Optional[str]


class BugReportList(TypedDict):
    """``GET /api/v1/bugs/reports`` answer (page-based)."""

    data: List[BugReport]
    pagination: Pagination


IMAGE_MAX_BYTES = 5 * 1024 * 1024
METADATA_MAX_LENGTH = {"description": 5000, "website": 191, "telegram": 191, "twitterx": 191, "image": 191}


def js_len(s: str) -> int:
    """Length in UTF-16 code units, as the API (and TS ``.length``) count it: a character outside the BMP (e.g. an
    emoji) counts 2. Every API length pre-check (metadata fields, agent name / description) uses it."""
    return len(s.encode("utf-16-le")) // 2

_MAX_AGENT_ID = 2147483647


def _agent_id(raw: Any) -> int:
    """An agent id as the API takes it, 0..2147483647: an int, or a whole float as TS takes ``2.0`` (through
    :func:`to_uint`, so a bool, a fraction or a string is refused); else ``ValidationError("agentId")``."""
    try:
        v = to_uint(raw, "agentId")
    except ValidationError:
        v = None
    if v is None or v > _MAX_AGENT_ID:
        raise ValidationError("agentId", f"agent_id must be an integer 0..{_MAX_AGENT_ID} (got {raw!r})")
    return v


def sniff_image(head: bytes) -> Optional[str]:
    """The image type from the leading bytes, by the server's rules: the 8-byte PNG signature, JPEG FF D8 FF,
    ``RIFF....WEBP``, GIF87a or GIF89a. None for anything else (an empty file too)."""
    head = bytes(head[:12])
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _is_path(source: Any) -> bool:
    return isinstance(source, (str, os.PathLike))


_IMAGE_URL = re.compile(r"https?://[^\s/?#]+", re.IGNORECASE)

_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024


def _download_too_large(url: str) -> ValidationError:
    """``ValidationError('image')`` for a download over ``_DOWNLOAD_MAX_BYTES``. Same text in TS."""
    return ValidationError("image", f"the file at {url} is larger than 20 MB ({_DOWNLOAD_MAX_BYTES} bytes): "
                                    "download_image reads at most that much.")


def _capped_body(url: str, max_bytes: int):
    """A requests response hook that stops a download at ``max_bytes``: a Content-Length above it raises
    :func:`_download_too_large` before the body is read, and the body (read through ``iter_content``) raises it as soon
    as more than that has come in. Each response of a redirect chain is held to it. The status comes first: an error
    answer (``not r.ok``) comes back with an empty body, not read (as in TS), whatever its Content-Length."""

    def hook(r: requests.Response, *_args: Any, **_kwargs: Any) -> requests.Response:
        if not r.ok:
            r.close()
            r.iter_content = lambda *_a, **_k: iter(())  # type: ignore[method-assign]
            return r
        declared = (r.headers.get("content-length") or "").strip()
        if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
            r.close()
            raise _download_too_large(url)
        inner = r.iter_content

        def capped(chunk_size: Any = 1, decode_unicode: bool = False):
            total = 0
            for chunk in inner(chunk_size, decode_unicode):
                total += len(chunk)
                if total > max_bytes:
                    r.close()
                    raise _download_too_large(url)
                yield chunk

        r.iter_content = capped  # type: ignore[method-assign]
        return r

    return hook


def _check_image_url(image_url: Any) -> str:
    """``image_url`` when it is an http(s) URL with a host; ``ValidationError("imageUrl")`` otherwise, before any
    download."""
    if not isinstance(image_url, str) or not _IMAGE_URL.match(image_url):
        if isinstance(image_url, str):
            got = f"'{image_url[:80]}...'" if len(image_url) > 80 else f"'{image_url}'"
        else:
            got = type(image_url).__name__
        raise ValidationError("imageUrl", f"imageUrl must be an http(s) URL of an image, got {got}.")
    return image_url


def _image_part(source: ImageSource, filename: Optional[str], default_name: str):
    """``(filename, bytes, mime)`` for a multipart file part, typed from its leading bytes (:func:`sniff_image`). The
    server judges an upload by its bytes alone, whatever its declared type or name, so an empty file, one over 5 MB,
    or bytes that are not png, jpeg, webp or gif raise ValidationError("image") before any request."""
    if _is_path(source):
        path = os.fspath(source)
        with open(path, "rb") as f:
            content = f.read()
        name = filename or os.path.basename(path)
    elif isinstance(source, (bytes, bytearray, memoryview)):
        content = bytes(source)
        name = filename or default_name
    else:
        raise ValidationError("image", f"image must be a file path or bytes (got {type(source).__name__})")
    if len(content) == 0:
        raise ValidationError("image", f'image "{name}" is empty')
    if len(content) > IMAGE_MAX_BYTES:
        raise ValidationError("image", f'image "{name}" is {len(content)} bytes; the API takes at most 5 MB ({IMAGE_MAX_BYTES} bytes)')
    mime = sniff_image(content[:12])
    if not mime:
        raise ValidationError("image",
                              f'cannot tell the image type of "{name}" from its bytes: use jpeg, png, webp or gif')
    return (name, content, mime)


class ContentApi(ApiCore):
    """Content routes (mixin of ``BasisAPI``)."""


    def upload_image(
        self,
        file: ImageSource,
        filename: Optional[str] = None,
        purpose: Optional[ImagePurpose] = "token",
        address: Optional[str] = None,
    ) -> str:
        """``POST /api/images`` (multipart ``file``, ``purpose``, ``address``): pins an image (jpeg, png, webp, gif;
        max 5 MB). Returns the hosted URL.

        ``file`` is a local path (``str`` / ``os.PathLike``) or the bytes; the part's type is taken from the bytes.
        ``filename`` names the part (default: the file's name, or ``image.png``). ``purpose`` "token" needs the
        token/market ``address`` and the caller must be its creator; "avatar" is capped at 5 a month. ``purpose`` None
        is "token", the default (TS null). Auth: key or session.
        """
        if purpose is None:
            purpose = "token"
        if purpose == "token" and not address:
            raise ValidationError("address", 'address is required when purpose is "token"')
        data = {"purpose": purpose}
        if purpose == "token":
            data["address"] = address
        files = {"file": _image_part(file, filename, "image.png")}
        result = self._request("POST", "/api/images", auth="keyOrSession", files=files, data=data, kind="upload")
        return result["url"]

    def upload_image_from_url(
        self, image_url: str, contract_address: Optional[str] = None,
        purpose: Optional[ImagePurpose] = "token",
    ) -> str:
        """Download an image from an http(s) URL, turn it upright, resize it to 512x512 (center-crop), convert it to
        WebP and upload it with :meth:`upload_image` (needs ``Pillow``; the URL and byte rules: see
        :meth:`download_image`). Returns the hosted URL. Missing credentials raise ``AuthRequiredError`` before the
        download; a failed download raises ``BasisError`` (past the upload deadline, ``api_upload_timeout_ms``:
        ``BasisApiError`` code ``timeout``). ``purpose`` None is "token", the default (TS null). Auth: key or session.
        """
        if purpose is None:
            purpose = "token"
        if purpose == "token" and not contract_address:
            raise ValidationError("address", 'address is required when purpose is "token"')
        self.check_upload_auth()
        webp = self.download_image(image_url)
        filename = f"{contract_address}.webp" if contract_address else f"image_{int(time.time() * 1000)}.webp"
        return self.upload_image(webp, purpose=purpose, address=contract_address, filename=filename)

    def check_upload_auth(self) -> None:
        """Raise ``AuthRequiredError`` unless the client has an API key or a SIWE session (what the upload routes need).
        Sends nothing: lets a caller check before a step that cannot be undone (factory.create_token_with_metadata)."""
        self._auth_headers("keyOrSession", "POST /api/images")

    def download_image(self, image_url: str) -> bytes:
        """Download an image from a URL and return it upright (EXIF orientation applied), resized to 512x512
        (center-crop) as WebP bytes, the form :meth:`upload_image_from_url` sends (needs ``Pillow``: checked before the
        download, ``BasisError`` when missing). ``image_url``: an http(s) URL only (anything else, a data: URL too,
        raises ``ValidationError("imageUrl")`` before any download). No API call and no credentials needed. A failed
        download (non-2xx, network) raises ``BasisError``; past the upload deadline (``api_upload_timeout_ms``, body
        included) ``BasisApiError`` code ``timeout``, a ``BasisError`` too; a file over 20 MB (by its Content-Length, or
        counted while it streams in: the rest is not read) raises ``ValidationError("image")``; downloaded bytes that
        are not png, jpeg, webp or gif (by their leading bytes, as the API judges an upload: so no SVG) raise
        ``ValidationError("image")``; bytes Pillow cannot read raise its error."""
        url = _check_image_url(image_url)
        try:
            from PIL import Image, ImageOps
        except ImportError:
            raise BasisError('[basis-sdk] download_image / upload_image_from_url need the "Pillow" package '
                             "(pip install Pillow). Or resize the image yourself and call upload_image() with the "
                             "bytes.") from None

        try:
            resp = request_with_deadline(self._plain, "GET", url, self._timeout_for("upload"), f"GET {url}",
                                         hooks={"response": _capped_body(url, _DOWNLOAD_MAX_BYTES)})
        except (BasisApiError, ValidationError):
            raise
        except requests.RequestException as e:
            raise BasisError(f"Failed to download image from {url}: {e}") from e
        if not resp.ok:
            raise BasisError(f"Failed to download image from {url}: {resp.status_code}")

        content = resp.content or b""
        if sniff_image(content[:12]) is None:
            raise ValidationError("image", f"the file at {url} is not a png, jpeg, webp or gif image (judged by its "
                                           "bytes, as the API judges an upload)")

        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img) or img
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((512, 512), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=90)
        return buf.getvalue()

    def set_avatar(self, source: ImageSource, filename: str = "avatar.webp") -> str:
        """Upload an avatar (``POST /api/images``, purpose "avatar") and set it on the profile
        (``POST /api/v1/me/profile {avatar}``). Returns the hosted avatar URL. Auth: key or session.

        ``source``: an http(s) image URL (downloaded and resized, see :meth:`upload_image_from_url`), a local file
        path (any other ``str``, or ``os.PathLike``; TS has no path input), or the bytes (named by ``filename``).

        When the upload went through but setting it on the profile failed, the exception carries the hosted URL as
        ``avatar_url`` (TS: ``avatarUrl``): finish with ``update_my_profile({"avatar": e.avatar_url})`` instead of
        uploading again (avatar uploads are capped at 5 a month).
        """
        if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
            url = self.upload_image_from_url(source, purpose="avatar")
        else:
            url = self.upload_image(source, purpose="avatar", filename=None if _is_path(source) else filename)
        try:
            self._request("POST", "/api/v1/me/profile", auth="keyOrSession", body={"avatar": url})
        except Exception as e:
            try:
                e.avatar_url = url  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            raise
        return url


    def update_metadata(
        self,
        address: str,
        description: Optional[str] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        image: Optional[str] = None,
        outcome_art: Optional[Dict[str, str]] = None,
    ) -> MetadataResult:
        """``POST /api/metadata``: pins the metadata of a new token or market (caller must be its creator; the server
        reads name, symbol and creator from the chain). ``description`` at most 5000 characters, the others 191;
        ``image`` is a URL from :meth:`upload_image`. ``outcome_art``: a prediction market's outcome pictures from the
        site's built-in picture library, ``{<outcome name>: <picture id>}`` (sent as ``outcomeArt``; the API matches
        each name to the market's outcomes on chain, keeps one picture per market and ignores unknown ids); a dict of
        strings, else ``ValidationError("outcomeArt")`` before sending.

        One-shot: a second call answers 409 (``BasisApiError`` code ``exists``); later changes go through
        :meth:`update_project`. Auth: key or session.
        """
        body: Dict[str, Any] = {"address": address}
        for k, v in (("description", description), ("website", website), ("telegram", telegram),
                     ("twitterx", twitterx), ("image", image)):
            if v is not None:
                body[k] = v
        if outcome_art is not None:
            if not isinstance(outcome_art, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                            for k, v in outcome_art.items()):
                raise ValidationError("outcomeArt", "outcome_art must be a dict of {<outcome name>: <picture id>} "
                                                    "strings.")
            body["outcomeArt"] = dict(outcome_art)
        return self._request("POST", "/api/metadata", auth="keyOrSession", body=body, kind="upload")

    def update_project(
        self,
        address: str,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        description: Optional[str] = None,
        image: Optional[ImageSource] = None,
        image_filename: Optional[str] = None,
    ) -> ProjectUpdateResult:
        """``POST /api/projects/{address}``: updates a project's links / description (caller must be its developer).
        With ``image`` (a local path, ``str`` / ``os.PathLike``, or the bytes) it is sent as multipart with the new
        image; otherwise as JSON. Returns the updated project row. Auth: key or session.
        """
        fields = {k: v for k, v in (("website", website), ("telegram", telegram), ("twitterx", twitterx),
                                    ("description", description)) if v is not None}
        if not fields and image is None:
            raise ValidationError("payload", "update_project needs at least one field or an image")
        path = f"/api/projects/{seg(address)}"
        if image is None:
            return self._request("POST", path, auth="keyOrSession", body=fields)
        files = {"image": _image_part(image, image_filename, "image.png")}
        return self._request("POST", path, auth="keyOrSession", data=fields, files=files, kind="upload")


    def create_comment(self, project_id: int, content: str, author_address: str) -> CommentRow:
        """``POST /api/comments``: posts a comment (at most 2000 characters) as ``author_address``, which must be
        the signed-in wallet. One qualifying trade unlocks commenting for good: a buy of at least $25 in one trade on a
        token, a bet of at least $5 on a market (the project's creator always may; else 403 ``not_eligible``); then
        one comment per 30 s (429 ``cooldown``). Returns the stored row. Auth: key or session.
        """
        return self._request(
            "POST",
            "/api/comments",
            auth="keyOrSession",
            body={"projectId": project_id, "content": content, "authorAddress": author_address},
        )

    def delete_comment(self, comment_id: int, author_address: Optional[str] = None) -> DeleteCommentResult:
        """``DELETE /api/comments``: soft-deletes your own comment. ``author_address`` is optional: left out, the server
        uses the authenticated wallet (the real site still asks for it). Auth: key or session."""
        return self._request("DELETE", "/api/comments", auth="keyOrSession",
                             params={"id": comment_id, "authorAddress": author_address})


    def get_agents(
        self, page: Optional[int] = None, limit: Optional[int] = None, offset: Optional[int] = None,
    ) -> AgentList:
        """``GET /api/agents``: registered agents, newest first. Page-based (``limit`` 1..100, server default 20;
        ``offset`` is an alternative to ``page``, rounded down to a page start). Public."""
        return self._request("GET", "/api/agents", params={"page": page, "limit": limit, "offset": offset})

    def get_agent(self, address: str) -> AgentLookup:
        """``GET /api/agents/{address}``: the agent registered for a wallet (``{isAgent: False, agent: None}`` when
        none). Public."""
        return self._request("GET", f"/api/agents/{seg(address)}")

    def register_agent(
        self,
        wallet: Optional[str] = None,
        agent_id: Union[int, float, None] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        capabilities: Optional[str] = None,
    ) -> AgentRegistration:
        """``POST /api/agents``: records an on-chain ERC-8004 registration. ``wallet`` is optional: left out (None), the
        API takes the authenticated wallet; given, it must be that wallet (else 403 ``forbidden``). ``agent_id`` is
        required (``register_agent(agent_id=12)`` without a wallet). The authenticated wallet must own ``agent_id``
        (0..2147483647) or be its declared agent wallet, and the agent must carry the BASIS
        mark (``protocol: "basis"`` or a BASIS services entry in its registration file, or the registry's ``basis``
        metadata key; the real site still asks for a base64 data URI with protocol "basis"). ``name`` at most 100
        characters (the real site's default "Basis Agent"), ``description`` 500; the paper site takes them from the
        registration file instead. 403 ``wallet_in_account`` for a wallet of a person's BASIS account (an agent needs
        its own), ``forbidden``, ``not_branded``; 409 when the id is synced to another wallet that still controls it.
        ``agent_id``: a whole number (a whole float such as ``2.0`` too, as TS takes it); anything else (a string, bool,
        None, left out, a fraction) raises ``ValidationError("agentId")`` before sending. ``wallet``, ``name``,
        ``description``, ``capabilities`` None are left out of the body. Auth: key or session."""
        body: Dict[str, Any] = {"agentId": _agent_id(agent_id)}
        for k, v in (("wallet", wallet), ("name", name), ("description", description), ("capabilities", capabilities)):
            if v is not None:
                body[k] = v
        return self._request("POST", "/api/agents", auth="keyOrSession", body=body)

    def register_agent_signed(self, agent_id: Union[int, float], message: str, signature: str) -> AgentRegistration:
        """``POST /api/agents`` with a signed message instead of a sign-in: the FIRST sync of an agent wallet, which
        cannot sign in before it is an agent (a wallet signs in only when it is linked to a BASIS account or is a
        registered agent). ``message`` is :func:`basis.build_agent_sync_message` signed by the agent wallet; signing it
        accepts the BASIS Terms. 401 ``invalid_signature`` names the line that is wrong; 403 ``not_branded`` /
        ``wallet_in_account`` / ``forbidden`` as for :meth:`register_agent`. 30 signed syncs an hour per IP (and 10
        agent syncs an hour per wallet). The paper site takes it; the real site does not yet. No auth, sent once (no
        retries). ``client.agent.sync_signed(agent_id)`` builds and signs it. ``agent_id`` as for
        :meth:`register_agent`."""
        aid = _agent_id(agent_id)
        if not isinstance(message, str) or not isinstance(signature, str):
            raise ValidationError("agentSignedSync", "register_agent_signed needs the signed message and its signature.")
        return self._request("POST", "/api/agents", auth="none",
                             body={"agentId": aid, "message": message, "signature": signature})


    def submit_bug_report(
        self,
        title: str,
        description: str,
        severity: BugSeverity,
        category: BugCategory,
        evidence: Optional[str] = None,
    ) -> BugReportSubmission:
        """``POST /api/v1/bugs/reports``: submits a bug report (5 a day per wallet; 409 ``duplicate`` for a near-copy,
        403 ``blocked``). ``title`` at most 191 characters, ``description`` 5000; ``evidence`` an HTTPS URL or a tx
        hash (at most 1000). Auth: key or session.
        """
        body: Dict[str, Any] = {"title": title, "description": description, "severity": severity, "category": category}
        if evidence is not None:
            body["evidence"] = evidence
        return self._request("POST", "/api/v1/bugs/reports", auth="keyOrSession", body=body)

    def get_bug_reports(
        self,
        status: Optional[BugStatus] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> BugReportList:
        """``GET /api/v1/bugs/reports``: your bug reports. Page-based. Auth: key or session."""
        return self._request("GET", "/api/v1/bugs/reports", auth="keyOrSession",
                             params={"status": status, "page": page, "limit": limit, "offset": offset})
