"""Factory tokens: creation, registry reads, and each token's dev settings and reads
(TypeScript SDK: src/modules/Factory.ts)."""

import encodings.idna
import json
import os
import re
import stringprep
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote_to_bytes

from web3 import Web3

from ..api.content import IMAGE_MAX_BYTES, METADATA_MAX_LENGTH, ImageSource, js_len, sniff_image
from ..api.core import AuthRequiredError, BasisApiError
from ..config.addresses import is_zero_address
from ..core.amounts import amount_of
from ..core.base import BaseModule
from ..core.errors import BasisError, NotDeployedError, TxNotConfirmedError, ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import refuse_paused_asset
from ..core.mains import MainInfo, main_amount, pair_amount
from ..core.units import MAX_UINT256, Amount, check_text, parse_amount, to_address, to_bool, to_uint

RECYCLE_WINDOW = 7 * 24 * 3600
_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_TICKER = re.compile(r"[A-Za-z0-9]{1,10}")


_ASCII_WS = " \t\n\r\f\v"


def _check_ticker(raw: Any) -> str:
    """The ticker as it goes on chain: trimmed of ASCII whitespace and upper-cased, after the site's rule (rule
    ``symbol``, checked before any read): a str, well-formed text (the ``check_text`` message for a lone surrogate), not
    empty, then 1-10 letters or digits (A-Z, a-z, 0-9; a lower-case letter is sent upper case, as the site's form does).
    TS: ``checkTicker``."""
    check_text(raw, "symbol")
    s = raw.strip(_ASCII_WS) if isinstance(raw, str) else ""
    if not s:
        raise ValidationError("symbol", "symbol is required (a non-empty string).")
    if not _TICKER.fullmatch(s):
        raise ValidationError(
            "symbol",
            f"symbol (the ticker) must be 1-10 letters or digits (A-Z, 0-9), as the site takes it; got "
            f"{json.dumps(s, ensure_ascii=False)}.")
    return s.upper()


_JS_SPACE = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
             "\u2028\u2029\u202f\u205f\u3000\ufeff")


def _js_trim(s: str) -> str:
    return s.strip(_JS_SPACE)


def _check_name(raw: Any) -> str:
    """The token name as it goes on chain: trimmed as the site's create form trims it (JS ``trim()``: ``_js_trim``,
    whitespace, line terminators, no-break spaces and the BOM), then refused when that leaves nothing (a non-str
    included) or it is not well-formed text (rule ``name``, before any read). TS: ``checkName``."""
    name = _js_trim(raw) if isinstance(raw, str) else ""
    if not name:
        raise ValidationError("name", "name is required (a non-empty string).")
    check_text(name, "name")
    return name


_MAX_LINK = 191
_MAX_RAW_LINK = 2048
_LINE = "[^\n\r\u2028\u2029]"
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I | re.A)
_TG_AT = re.compile(r"@([A-Za-z0-9_]{4,32})")
_TG_LINK = re.compile(rf"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/([^?#]+?)/?(?:[?#]{_LINE}*)?", re.I | re.A)
_TG_HANDLE = re.compile(r"[A-Za-z0-9_]{4,32}")
_TG_PATH = re.compile(r"(?:[A-Za-z0-9_]{4,32}|\+[A-Za-z0-9_-]{6,64}|joinchat/[A-Za-z0-9_-]{6,64})")
_X_LINK = re.compile(rf"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/@?([^/?#]+)/?(?:[?#]{_LINE}*)?", re.I | re.A)
_X_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}")
_LINK_ERROR = {
    "website": "The website must be an https:// link.",
    "telegram": "Telegram must be a t.me link, like https://t.me/your_channel.",
    "twitterx": "X must be a profile link, like https://x.com/your_handle.",
}

_QUERY_SET = ' "#<>'
_SPECIAL_QUERY_SET = _QUERY_SET + "'"
_PATH_SET = _QUERY_SET + "?^`{}"
_USERINFO_SET = _PATH_SET + "/:;=@[\\]^|"
_FRAGMENT_SET = ' "<>`'
_FORBIDDEN_DOMAIN = set("\x00\t\n\r #/:<>?@[\\]^|%\x7f") | {chr(c) for c in range(0x20)}


def _pct(s: str, keep_out: str) -> str:
    """``s`` percent-encoded as the URL parser does: code points below 0x20, above 0x7E or in ``keep_out``, as UTF-8."""
    out = []
    for ch in s:
        if ord(ch) < 0x20 or ord(ch) > 0x7E or ch in keep_out:
            if 0xD800 <= ord(ch) <= 0xDFFF:
                ch = "\ufffd"
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
        else:
            out.append(ch)
    return "".join(out)


def _ipv4(host: str) -> Optional[str]:
    """The WHATWG IPv4 parse of a host that ends in a number (dotted decimal), or None on failure."""
    parts = host.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        return None
    nums = []
    for p in parts:
        if p[:2] in ("0x", "0X"):
            digits, radix, ok = p[2:], 16, r"[0-9A-Fa-f]*"
        elif len(p) >= 2 and p[0] == "0":
            digits, radix, ok = p[1:], 8, r"[0-7]*"
        else:
            digits, radix, ok = p, 10, r"[0-9]+"
        if not re.fullmatch(ok, digits, re.A):
            return None
        nums.append(int(digits, radix) if digits else 0)
    if any(n > 255 for n in nums[:-1]) or nums[-1] >= 256 ** (5 - len(nums)):
        return None
    v = nums[-1] + sum(n * 256 ** (3 - i) for i, n in enumerate(nums[:-1]))
    return ".".join(str((v >> s) & 255) for s in (24, 16, 8, 0))


def _domain_to_ascii(domain: str) -> str:
    """The host in ASCII, near enough to UTS46 for the site's check: ASCII labels lower-cased, others IDNA-encoded."""
    for dot in ("\u3002", "\uff0e", "\uff61"):
        domain = domain.replace(dot, ".")
    domain = "".join(ch for ch in domain if not stringprep.in_table_b1(ch))
    labels = []
    for label in domain.split("."):
        if label.isascii():
            labels.append(label.lower())
            continue
        try:
            labels.append(encodings.idna.ToASCII(label).decode("ascii").lower())
        except UnicodeError:
            labels.append("xn--" + label.lower().encode("punycode").decode("ascii"))
    return ".".join(labels)


def _https_url(s: str) -> Optional[str]:
    """``new URL(s).toString()`` when ``s`` parses as an https URL (the WHATWG rules the site's check depends on:
    scheme, credentials, host with IDNA and IPv4, port, and the percent-encoding that sets the length), else None."""
    s = s.strip("".join(chr(c) for c in range(0x21))).replace("\t", "").replace("\n", "").replace("\r", "")
    m = _SCHEME.match(s)
    if not m or m.group(0)[:-1].lower() != "https":
        return None
    rest = s[m.end():].lstrip("/\\")
    end = next((i for i, ch in enumerate(rest) if ch in "/\\?#"), len(rest))
    authority, rest = rest[:end], rest[end:]
    userinfo = ""
    if "@" in authority:
        cred, authority = authority.rsplit("@", 1)
        user, _, password = cred.replace("@", "%40").partition(":")
        user, password = _pct(user, _USERINFO_SET), _pct(password, _USERINFO_SET)
        if user or password:
            userinfo = user + (":" + password if password else "") + "@"
    host, colon, port = authority.partition(":")
    if not host or host.startswith("["):
        return None
    if colon and port:
        if not re.fullmatch(r"[0-9]+", port, re.A) or int(port) > 65535:
            return None
        port = "" if int(port) == 443 else str(int(port))
    try:
        domain = unquote_to_bytes(host).decode("utf-8")
    except UnicodeDecodeError:
        return None
    host = _domain_to_ascii(domain)
    if not host or any(ch in _FORBIDDEN_DOMAIN for ch in host):
        return None
    labels = host.split(".")
    last = labels[-2] if labels[-1] == "" and len(labels) > 1 else labels[-1]
    if re.fullmatch(r"[0-9]+|0[xX][0-9A-Fa-f]*", last, re.A):
        host = _ipv4(host)
        if host is None:
            return None
    if "." not in host:
        return None
    path, _, frag = rest.partition("#")
    path, q, query = path.partition("?")
    raw = re.split(r"[/\\]", path[1:]) if path else [""]
    segs: List[str] = []
    for i, seg in enumerate(raw):
        is_last = i == len(raw) - 1
        low = seg.lower()
        if low in ("..", ".%2e", "%2e.", "%2e%2e"):
            if segs:
                segs.pop()
            if is_last:
                segs.append("")
        elif low in (".", "%2e"):
            if is_last:
                segs.append("")
        else:
            segs.append(_pct(seg, _PATH_SET))
    out = f"https://{userinfo}{host}{':' + port if port else ''}" + "".join("/" + p for p in segs)
    if q:
        out += "?" + _pct(query, _SPECIAL_QUERY_SET)
    if "#" in rest:
        out += "#" + _pct(frag, _FRAGMENT_SET)
    return out


def _site_link(field: str, raw: str) -> Optional[str]:
    """The link as the site stores it, or None when the site refuses it. ``raw`` is trimmed as the site trims it."""
    if field == "website":
        s = _js_trim(raw if _SCHEME.match(raw) else f"https://{raw}")
        if not s or js_len(s) > _MAX_RAW_LINK:
            return None
        return _https_url(s)
    if field == "telegram":
        at = _TG_AT.fullmatch(raw)
        path = at.group(1) if at else None
        if not path:
            m = _TG_LINK.fullmatch(raw)
            path = m.group(1) if m else (raw if _TG_HANDLE.fullmatch(raw) else None)
        return f"https://t.me/{path}" if path and _TG_PATH.fullmatch(path) else None
    m = _X_LINK.fullmatch(raw)
    handle = m.group(1) if m else (raw[1:] if raw.startswith("@") else raw)
    return f"https://x.com/{handle}" if _X_HANDLE.fullmatch(handle) else None


def _check_metadata_fields(fields: Dict[str, Optional[str]]) -> None:
    """The rules of POST /api/metadata, checked before the create tx (lengths in UTF-16 units, as the API and TS count):
    description at most 5000 characters; website an https link (a bare host gets https://), telegram a t.me link or
    handle, twitterx an X handle, each at most 191 characters as the site stores it. Validates only: the caller's value
    is sent as given (the site normalises it). An empty link means no link."""
    d = fields.get("description")
    if d is not None:
        if not isinstance(d, str):
            raise ValidationError("metadataTooLong", "description must be a string.")
        if js_len(d) > METADATA_MAX_LENGTH["description"]:
            raise ValidationError("metadataTooLong", f"description is {js_len(d)} characters; the API takes at most "
                                                     f"{METADATA_MAX_LENGTH['description']}.")
    for key in ("website", "telegram", "twitterx"):
        v = fields.get(key)
        if v is None:
            continue
        if not isinstance(v, str):
            raise ValidationError("metadataTooLong", f"{key} must be a string.")
        raw = _js_trim(v)
        if raw == "":
            continue
        if js_len(raw) > _MAX_RAW_LINK:
            raise ValidationError("metadataTooLong", f"{key} is {js_len(raw)} characters; the API takes at most {_MAX_RAW_LINK}.")
        link = _site_link(key, raw)
        if link is None:
            raise ValidationError("metadataLink", f"{_LINK_ERROR[key]} Got {json.dumps(v)}.")
        if len(link) > _MAX_LINK:
            raise ValidationError("metadataTooLong",
                                  f"{key} is {len(link)} characters as the API stores it ({link}); it takes at most {_MAX_LINK}.")


def _upload_error_of(stage: str, e: BaseException) -> Dict[str, Any]:
    """A failed upload as an ``upload_error`` dict (the original exception kept as ``cause``)."""
    message = str(e)
    if message.startswith("[basis-sdk] "):
        message = message[len("[basis-sdk] "):]
    if isinstance(e, BasisApiError):
        return {"stage": stage, "code": e.code, "status": e.status, "message": message, "cause": e}
    if isinstance(e, AuthRequiredError):
        return {"stage": stage, "code": "auth_required", "status": None, "message": message, "cause": e}
    return {"stage": stage, "code": None, "status": None, "message": message, "cause": e}


_CREATE_EXTRA_KEYS = ("frozen", "presale_tokens", "supply_multiplier", "rewards_enabled", "auto_vest", "auto_vest_duration",
                      "gradual_autovest", "reversed", "main")


class FactoryModule(BaseModule):
    """Factory tokens: creation, registry reads, and each token's dev settings and reads."""

    @property
    def deployed(self) -> bool:
        """True when the factory has an address on this chain."""
        return not is_zero_address(self.client.addresses["factory"])

    @property
    def _factory_address(self) -> str:
        return self._require(self.client.addresses["factory"], "Factory")

    @property
    def _factory(self):
        return self._contract(self._factory_address, "Factory")

    def _token(self, token: str):
        return self._contract(Web3.to_checksum_address(token), "FactoryToken")


    def create_token(
        self,
        symbol: str,
        name: str,
        hybrid_multiplier: Union[int, float],
        start_lp: Union[int, float],
        frozen: bool = False,
        presale_tokens: Union[int, float] = 0,
        supply_multiplier: Union[int, float] = 1,
        rewards_enabled: bool = False,
        auto_vest: bool = False,
        auto_vest_duration: Optional[Union[int, float]] = None,
        gradual_autovest: bool = False,
        reversed: bool = False,
        main: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a factory token (a reversed one when ``reversed``).

        - symbol: the ticker, 1-10 letters or digits (A-Z, 0-9), the site's rule, checked before any read (rule
          ``symbol``; a lone surrogate gets the not-valid-text message). ASCII whitespace around it is trimmed and a
          lower-case letter is sent upper case (``"doge"`` goes on chain as ``DOGE``), as the site's form does.
        - name: trimmed as the site's form trims it (JS ``trim()``: whitespace, no-break spaces, the BOM); empty after the
          trim, or not well-formed text (a lone surrogate: the chain could not store it as typed), is refused before
          any read (rule ``name``).
        - hybrid_multiplier: 1-100. Normal token: higher keeps more of the price as a floor. Reversed (meme+) token:
          higher is closer to a fixed-supply token. The floor is measured in the pair asset, which can move itself.
        - start_lp: launch liquidity in basis points of the main's seed: 1000-100000, i.e. 10% to 1000%. Higher also
          means a larger launch supply (see ``reversed``).
        - frozen / rewards_enabled / auto_vest / gradual_autovest / reversed: real booleans only (a str ``"false"``,
          or 1 are refused with ``ValidationError(field)``; None = the default False: the flags are fixed for life).
        - frozen: invite-only presale (only whitelisted wallets buy until the target is hit); needs presale_tokens > 0.
        - presale_tokens: presale size as a whole-token count (not wei). 0 = no presale.
        - supply_multiplier: launch supply multiplier, 1 up to the main's maximum (read live; the refusal names it).
        - rewards_enabled: holder rewards (paid in the pair asset), fixed for life.
        - auto_vest_duration: whole days, 1-990 (only used when ``auto_vest``).
        - reversed: create a reversed (meme+) token. Its presale must fit in half the launch supply, which grows with
          ``start_lp`` and ``supply_multiplier``; a bigger presale is refused at simulation (``BasisContractError``,
          reason 'presale above half the launch supply'), before any gas is spent.
        - main: address or address-book key (default: the default main).

        Checks the create rules before the tx (the reversed-presale limit is checked at simulation) and sends the exact
        creation fee.
        Returns {'hash', 'receipt', 'result', 'warnings', 'token', 'creator'}: token / creator read from the tx's log
        (None with a warning when it is missing; the tx is confirmed).
        """
        return self._send_create(*self._build_create_params(
            symbol=symbol, name=name, hybrid_multiplier=hybrid_multiplier, start_lp=start_lp, frozen=frozen,
            presale_tokens=presale_tokens, supply_multiplier=supply_multiplier, rewards_enabled=rewards_enabled,
            auto_vest=auto_vest, auto_vest_duration=auto_vest_duration, gradual_autovest=gradual_autovest,
            reversed=reversed, main=main,
        ))

    def create_token_with_metadata(
        self,
        symbol: str,
        name: str,
        hybrid_multiplier: Union[int, float],
        start_lp: Union[int, float],
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        image_file: Optional[ImageSource] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """Create a token, then upload its image and register its metadata (``POST /api/images``,
        ``POST /api/metadata``; they need the token address, so they run after the tx). Takes every ``create_token``
        argument plus description / image_url or image_file (a local path or the bytes) / website / telegram / twitterx.

        Checked before any gas is spent: the image argument, the metadata field lengths (description 5000 characters,
        website / telegram / twitterx 191), the symbol (the site's ticker rule, 1-10 letters or digits, rule
        ``symbol``), the name (not empty once trimmed, rule ``name``), the API credentials (an API key or a SIWE
        session), every create rule, the image itself (an ``image_url`` is downloaded and resized to 512x512 WebP here;
        an ``image_file`` must be png, jpeg, gif or webp by its bytes and at most 5 MB) and, last, the API key's wallet
        (see below).

        The key check: a key the client counts as the signer's (the ``api_key`` option with a signer is taken as the
        signer's) goes to ``GET /api/v1/me/profile``, sent with the key alone, once per key and signer; when the answer
        names another wallet, or none, the create is refused with ``ValidationError("apiKey")`` (the uploads after the
        fee would be refused). An API failure there (an invalid or revoked key, 429, network) is raised as it came.
        Nothing is sent in either case. Uploads that go with the SIWE session are not checked (the sign-in confirmed its
        wallet).

        Never raises after the create tx: if an upload still fails, the result carries the token, ``image_url`` /
        ``metadata`` (None for the failed step) and ``upload_error`` ({stage: 'token' | 'image' | 'metadata', code,
        status, message, cause}), plus a warning. Finish with ``retry_token_metadata(token, ...)``; calling this method
        again would launch a second token. A receipt that cannot be read after sending raises ``TxNotConfirmedError``
        whose ``note`` points to ``retry_token_metadata`` (no upload was made).
        Returns {'hash', 'receipt', 'result', 'warnings', 'token', 'creator', 'image_url', 'metadata', 'upload_error'}.
        """
        unknown = sorted(k for k in params if k not in _CREATE_EXTRA_KEYS)
        if unknown:
            raise TypeError(f"create_token_with_metadata() got unexpected arguments: {', '.join(unknown)}")
        if not image_url and image_file is None:
            raise ValidationError("imageRequired", "Either image_url or image_file is required.")
        if image_url and image_file is not None:
            raise ValidationError("imageRequired", "Give image_url or image_file, not both.")
        fields = {"description": description, "website": website, "telegram": telegram, "twitterx": twitterx}
        _check_metadata_fields(fields)
        _check_ticker(symbol)
        _check_name(name)
        self.client.api.check_upload_auth()
        built = self._build_create_params(
            symbol=symbol, name=name, hybrid_multiplier=hybrid_multiplier, start_lp=start_lp,
            frozen=params.pop("frozen", False), presale_tokens=params.pop("presale_tokens", 0),
            supply_multiplier=params.pop("supply_multiplier", 1), rewards_enabled=params.pop("rewards_enabled", False),
            auto_vest=params.pop("auto_vest", False), auto_vest_duration=params.pop("auto_vest_duration", None),
            gradual_autovest=params.pop("gradual_autovest", False), reversed=params.pop("reversed", False),
            main=params.pop("main", None),
        )
        image = self._prepare_image(image_url, image_file)
        self._confirm_upload_key()

        try:
            created = self._send_create(*built)
        except TxNotConfirmedError as e:
            raise e.with_note(
                "The image and metadata were NOT uploaded (they need the token address). Once the tx is mined, read the "
                "token (its TokenCreated log, or factory.get_tokens_by_creator) and call factory.retry_token_metadata(token, "
                "...); do not create the token again.") from e
        token = created["token"]
        if token is None:
            upload_error = {
                "stage": "token", "code": None, "status": None,
                "message": (f"the token address is unknown (no TokenCreated log in {created['hash']}): find it with "
                            "factory.get_tokens_by_creator, then call factory.retry_token_metadata."),
            }
            created["warnings"].append(f"Token created (tx {created['hash']}) but {upload_error['message']} Do not create it again.")
            return {**created, "image_url": None, "metadata": None, "upload_error": upload_error}
        uploaded = self._upload_token_metadata(token, image, None, fields)
        err = uploaded["upload_error"]
        if err:
            created["warnings"].append(
                f"Token {token} was created (tx {created['hash']}) but the {err['stage']} upload failed: {err['message']} "
                "Finish it with factory.retry_token_metadata(token, ...); do not create the token again."
            )
        return {**created, **uploaded, "token": token}

    def retry_token_metadata(
        self,
        token: str,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        image_file: Optional[ImageSource] = None,
        hosted_image_url: Optional[str] = None,
        website: Optional[str] = None,
        telegram: Optional[str] = None,
        twitterx: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Finish the uploads of a token whose ``create_token_with_metadata`` returned an ``upload_error`` (or any token
        the signer created). Give exactly one image source: ``image_url`` / ``image_file`` (uploaded as in
        create_token_with_metadata) or ``hosted_image_url`` (an image already hosted for it, e.g. the result's
        ``image_url`` when only the metadata failed). Same pre-checks as create_token_with_metadata, and
        ``hosted_image_url`` a str (ValidationError("hostedImageUrl")); sends no tx. Upload failures come back in
        ``upload_error`` (no raise). The API keeps one metadata record per token
        (409 ``exists`` once it is set). Returns {'token', 'image_url', 'metadata', 'upload_error'}.
        """
        token = to_address(token, "token")
        sources = [v for v in (image_url, image_file, hosted_image_url) if v is not None and v != ""]
        if len(sources) != 1:
            raise ValidationError("imageRequired", "Give exactly one of image_url, image_file or hosted_image_url.")
        fields = {"description": description, "website": website, "telegram": telegram, "twitterx": twitterx}
        _check_metadata_fields(fields)
        if hosted_image_url is not None and not isinstance(hosted_image_url, str):
            raise ValidationError("hostedImageUrl",
                                  "hostedImageUrl must be a string: the URL of an image already hosted for this token.")
        if hosted_image_url and js_len(hosted_image_url) > METADATA_MAX_LENGTH["image"]:
            raise ValidationError(
                "metadataTooLong",
                f"hostedImageUrl is {js_len(hosted_image_url)} characters; the API takes at most {METADATA_MAX_LENGTH['image']}.",
            )
        self.client.api.check_upload_auth()
        image = None if hosted_image_url else self._prepare_image(image_url, image_file)
        return self._upload_token_metadata(token, image, hosted_image_url or None, fields)

    def get_creation_fee(self, sender: Optional[str] = None) -> int:
        """The exact ``msg.value`` a create needs from ``sender`` (default: the signer):
        0 when fee-whitelisted, else ``get_fee_amount()``. Native wei."""
        who = self._account if sender is None else to_address(sender, "sender")
        return 0 if self.is_fee_whitelisted(who) else self.get_fee_amount()

    _confirmed_key: Optional[Tuple[str, str]] = None

    def _confirm_upload_key(self) -> None:
        """Before a create whose uploads go with the API key (the client counts the key as the signer's): the key's
        wallet (``api._key_wallet``: one ``GET /api/v1/me/profile`` with the key alone) must be the signer, else
        ``ValidationError("apiKey")``: a key of another wallet would have the uploads refused after the fee. Confirmed
        once per key and signer on this client. Uploads that go with the session (no key, or a key of another wallet
        beside the signer's session) are not checked. TS: ``confirmUploadKey``."""
        key = self.client.api_key
        if not key or self.client.api._key_state() != "ok":
            return
        signer = self._account.lower()
        if self._confirmed_key == (key, signer):
            return
        wallet = self.client.api._key_wallet()
        if wallet != signer:
            if wallet:
                msg = (f"The API key belongs to {wallet}, not to the signer {signer}: the image and metadata uploads "
                       "after the create would be refused. Pass the signer's own API key (or sign in with it). Nothing "
                       "was sent.")
            else:
                msg = ("The API did not say which wallet the API key belongs to (GET /api/v1/me/profile named none), so "
                       f"the uploads after the create could not be confirmed for the signer {signer}. Nothing was sent.")
            raise ValidationError("apiKey", msg)
        self._confirmed_key = (key, signer)

    def _send_create(self, p: tuple, is_reversed: bool) -> Dict[str, Any]:
        """Send a validated create with the exact fee and read the new token from the factory's log."""
        value = self.get_creation_fee(self._account)
        c = self._factory
        fn = c.functions.createReversedTokenFor(p) if is_reversed else c.functions.createTokenFor(p)
        tx = self._send(fn, "Factory", value=value)
        ev = self._expect_event(tx, c, "TokenCreated", "Factory", ("token", "creator"))
        return {
            **tx,
            "token": Web3.to_checksum_address(ev["tokenAddress"]) if ev else None,
            "creator": Web3.to_checksum_address(ev["creator"]) if ev else None,
        }

    def _prepare_image(self, image_url: Optional[str], image_file: Optional[ImageSource]) -> Tuple[bytes, str]:
        """The image bytes to upload and their type, checked before any tx: downloaded and resized (``image_url``) or
        as given (``image_file``: a local path or the bytes)."""
        if image_file is not None:
            if isinstance(image_file, (str, os.PathLike)):
                with open(os.fspath(image_file), "rb") as f:
                    data = f.read()
            elif isinstance(image_file, (bytes, bytearray, memoryview)):
                data = bytes(image_file)
            else:
                raise ValidationError("imageType", f"image_file must be a file path or bytes (got {type(image_file).__name__}).")
        else:
            data = self.client.api.download_image(image_url)
        mime = sniff_image(data[:12])
        if not mime:
            raise ValidationError("imageType", "The image must be png, jpeg, gif or webp (told by its bytes).")
        if len(data) > IMAGE_MAX_BYTES:
            raise ValidationError(
                "imageTooLarge", f"The image is {len(data)} bytes; the API takes at most 5 MB ({IMAGE_MAX_BYTES} bytes)."
            )
        return data, mime

    def _upload_token_metadata(self, token: str, image: Optional[Tuple[bytes, str]], hosted: Optional[str],
                               fields: Dict[str, Optional[str]]) -> Dict[str, Any]:
        """Upload the image (unless already hosted) and the metadata of ``token``. Never raises: failures go in
        ``upload_error``."""
        image_url = hosted
        try:
            if image is not None:
                data, mime = image
                image_url = self.client.api.upload_image(data, filename=f"{token}.{_IMAGE_EXT[mime]}", purpose="token",
                                                         address=token)
        except Exception as e:  # noqa: BLE001
            return {"token": token, "image_url": None, "metadata": None, "upload_error": _upload_error_of("image", e)}
        try:
            metadata = self.client.api.update_metadata(address=token, image=image_url, **fields)
            return {"token": token, "image_url": image_url, "metadata": metadata, "upload_error": None}
        except Exception as e:  # noqa: BLE001
            return {"token": token, "image_url": image_url, "metadata": None, "upload_error": _upload_error_of("metadata", e)}

    def _build_create_params(self, *, symbol, name, hybrid_multiplier, start_lp, frozen, presale_tokens,
                             supply_multiplier, rewards_enabled, auto_vest, auto_vest_duration, gradual_autovest,
                             reversed, main):
        """Validate params against the create rules (the main first) and build the create tuple."""
        symbol = _check_ticker(symbol)
        name = _check_name(name)

        hybrid = to_uint(hybrid_multiplier, "hybridMultiplier")
        lp = to_uint(start_lp, "startLP")
        presale = to_uint(presale_tokens if presale_tokens is not None else 0, "presaleTokens")
        if presale > MAX_UINT256 // 10 ** 18:
            raise ValidationError(
                "presaleTokens", f"presaleTokens counts whole tokens and is at most {MAX_UINT256 // 10 ** 18}; got {presale}."
            )
        supply = to_uint(supply_multiplier if supply_multiplier is not None else 1, "supplyMultiplier")
        frozen = to_bool(frozen, "frozen", False)
        rewards_enabled = to_bool(rewards_enabled, "rewardsEnabled", False)
        auto_vest = to_bool(auto_vest, "autoVest", False)
        gradual_autovest = to_bool(gradual_autovest, "gradualAutovest", False)
        reversed = to_bool(reversed, "reversed", False)
        duration = (0 if auto_vest_duration is None else to_uint(auto_vest_duration, "autoVestDuration")) if auto_vest else 0

        try:
            info: MainInfo = self.client.mains.get(main) if main else self.client.mains.default()
        except BasisError as e:
            if not main or isinstance(e, NotDeployedError):
                raise
            raise ValidationError("unknown main", f"{main} is not a main: {str(e).replace('[basis-sdk] ', '', 1)}") from e
        main_addr = info.main
        swap = self._contract(self._require(self.client.addresses["swap"], "Swap"), "Swap")
        main_fns = self._contract(main_addr, "MainToken").functions
        active = swap.functions.isMain(main_addr).call()
        registered_pair = self._factory.functions.getPair(main_addr, info.pair_asset).call()
        max_supply = main_fns.MAX_SUPPLY_MULTIPLIER().call()
        if not active:
            raise ValidationError("unknown pair", f"Main {info.symbol} ({main_addr}) is not active on SWAP.")
        if registered_pair.lower() != main_addr.lower():
            raise ValidationError("MainNotRegistered", f"Main {info.symbol} ({main_addr}) is not registered in the factory.")

        if hybrid < 1 or hybrid > 100:
            raise ValidationError("invalid multiplier", f"hybridMultiplier must be 1-100, got {hybrid}.")
        if auto_vest and (duration < 1 or duration > 990):
            raise ValidationError("auto-vest duration 1-990 days", f"autoVestDuration must be 1-990 days, got {duration}.")
        if frozen and presale == 0:
            raise ValidationError("frozen needs presale", "A frozen (invite-only) token needs presaleTokens > 0.")
        if lp < 1000 or lp > 100000:
            raise ValidationError("invalid starting LP", f"startLP is basis points of the main's seed, 1000-100000 (10%-1000%); got {lp}.")
        if supply < 1 or supply > max_supply:
            raise ValidationError("invalid supply multiplier",
                                  f"supplyMultiplier must be 1-{max_supply} for {info.symbol}, got {supply}.")

        p = (main_addr, symbol, name, hybrid, frozen, presale, lp, supply,
             rewards_enabled, auto_vest, duration, gradual_autovest)
        return p, reversed


    def disable_freeze(self, token: str) -> Dict[str, Any]:
        """End the whitelist (frozen) phase early. Token dev only. Refused before any tx when the token is not frozen
        ('not frozen': never was, the presale target was hit, or the phase already ended): the call would change
        nothing. Adds ``freeze_lifted``: True when the tx's log shows the freeze lifted (None with a warning when that
        log is missing)."""
        token = to_address(token, "token")
        t = self._token(token)
        if not t.functions.frozen().call():
            raise ValidationError(
                "not frozen",
                f"Token {token} is not frozen (never was, its presale target was hit, or the phase already ended): "
                "there is nothing to lift.")
        tx = self._send(t.functions.disableFreeze(), "FactoryToken")
        ev = self._expect_event(tx, t, "FreezeLifted", "FactoryToken", ("freeze_lifted",))
        return {**tx, "freeze_lifted": True if ev is not None else None}

    def set_whitelisted_wallet(self, token: str, wallets: List[str], amount: Union[int, str, float],
                               tag: Optional[str] = "") -> Dict[str, Any]:
        """Whitelist wallets for the frozen presale. Token dev only. Already-whitelisted wallets are skipped (their cap
        stays).

        amount: cap of tokens each wallet may buy in the presale: raw 18-decimal wei (int) or a human str/float/Decimal;
        above 0 ('cap': a 0 cap whitelists a wallet that can buy nothing).
        The whitelist only gates buys while the token is frozen and its presale runs, so the call is refused when it
        would have no effect: 'Presale over' (the presale target was hit) or 'not frozen' (never frozen, or
        ``disable_freeze`` ended the phase; it cannot be turned back on).
        tag: a label stored with the entries (default, also for None: ""); well-formed text (rule ``tag``).
        Adds ``added`` (wallets this tx whitelisted, from its logs), ``skipped`` (already whitelisted)
        and ``cap`` (``Amount`` in the token, unit ``token``, 18 decimals).
        """
        token = to_address(token, "token")
        if not isinstance(wallets, (list, tuple)) or not wallets:
            raise ValidationError("no wallets provided", "wallets must be a non-empty list.")
        if tag is None:
            tag = ""
        if not isinstance(tag, str):
            raise ValidationError("tag", f"tag must be a string, got {tag!r}.")
        check_text(tag, "tag")
        addrs = [to_address(w, f"wallets[{i}]") for i, w in enumerate(wallets)]
        cap = parse_amount(amount, 18, "cap")
        if cap == 0:
            raise ValidationError("cap", "The presale cap must be above 0: a wallet whitelisted with cap 0 can buy nothing.")
        t = self._token(token)
        if t.functions.presaleComplete().call():
            raise ValidationError("Presale over", f"Token {token} finished its presale: the whitelist no longer gates anything.")
        if not t.functions.frozen().call():
            raise ValidationError(
                "not frozen",
                f"Token {token} is not frozen (never was, or disable_freeze ended the phase): anyone may buy its presale, "
                "so the whitelist has no effect.",
            )
        fmt = amount_of(self.client, token)
        tx = self._send(t.functions.setWhitelistedWallet(addrs, cap, tag), "FactoryToken")
        added = [Web3.to_checksum_address(e["user"]) for e in self._events(tx, t, "WhiteListedWallet")]
        is_added = {a.lower() for a in added}
        return {**tx, "added": added, "skipped": [a for a in addrs if a.lower() not in is_added], "cap": fmt(cap)}

    def remove_whitelist(self, token: str, wallet: str) -> Dict[str, Any]:
        """Remove a wallet from the presale whitelist. Token dev only. Adds ``removed``, read from the tx's log (None
        with a warning when it is missing)."""
        t = self._token(to_address(token, "token"))
        wallet = to_address(wallet, "wallet")
        tx = self._send(t.functions.removeWhitelist(wallet), "FactoryToken")
        ev = self._expect_event(tx, t, "WhitelistRemoved", "FactoryToken", ("removed",))
        return {**tx, "removed": Web3.to_checksum_address(ev["user"]) if ev else None}


    def claim_rewards(self, token: str) -> Dict[str, Any]:
        """Claim holder rewards (paid in the pair asset). Refused before any tx: 'rewards off' (the token has holder
        rewards off), 'nothing to claim' (nothing claimable now, ``get_claimable_rewards`` 0: the tx would pay nothing;
        a holder flagged for recycling may still claim 0, which clears the flag), then 'pair asset paused' (its issuer
        paused the pair asset, so nothing can be paid out). Adds ``claimed``: what this claim paid, an Amount in the
        pair asset (0 only for a recycle-flagged holder with nothing to pay)."""
        token = to_address(token, "token")
        me = self._account
        t = self._token(token)
        if not t.functions.rewardsEnabled().call():
            raise ValidationError("rewards off", f"Token {token} has holder rewards off; there is nothing to claim.")
        info = self._main_info_of(token)
        claimable = t.functions.getClaimableRewards(me).call()
        if claimable == 0 and t.functions.recycleFlaggedAt(me).call() == 0:
            raise ValidationError("nothing to claim", f"{me} has no holder rewards to claim on {token} right now.")
        if claimable > 0:
            refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "no holder rewards can be paid out")
        tx = self._send(t.functions.claimRewards(), "FactoryToken")
        paid = sum(e["claimableNow"] for e in self._events(tx, t, "RewardsClaimed") if e["investor"].lower() == me.lower())
        return {**tx, "claimed": pair_amount(paid, info)}

    def add_to_rewards(self, token: str, amount: Union[int, str, float, Decimal]) -> Dict[str, Any]:
        """Top up a factory token's holder rewards (anyone may call it): ``amount`` of the main's pair asset is shared
        out to the holders at once. ``amount``: raw pair-asset units (int, the pair asset's decimals) or a human
        str/float/Decimal. Refused before any tx for 0 ('cannot be zero'), and with 'rewards off' or 'no shares' (no
        eligible holders yet): the money would go to the protocol instead of the holders; and, before the approval,
        while the pair asset's issuer has paused it ('pair asset paused': it cannot move). Approves the pair asset to
        the token when short.

        Adds ``distributed`` (Amount in the pair asset shared out to the holders: the top-up plus any recycled rewards
        paid out with it) and ``rewards_per_share`` (rewards added per reward share, scaled by 1e36;
        ``distributed`` is the amount); None with a warning when missing.
        """
        token = to_address(token, "token")
        t = self._token(token)
        info = self._main_info_of(token)
        raw = parse_amount(amount, info.pair_decimals)
        if raw == 0:
            raise ValidationError("cannot be zero", "The rewards top-up must be above 0.")
        if not t.functions.rewardsEnabled().call():
            raise ValidationError(
                "rewards off",
                f"Token {token} has holder rewards off: addToRewards would send the money to the protocol instead of the holders.",
            )
        if t.functions.totalShares().call() == 0:
            raise ValidationError(
                "no shares",
                f"Token {token} has no reward shares yet (no eligible holders): addToRewards would send the money to the "
                "protocol instead of the holders. Top up after the first buy.",
            )
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "the top-up cannot be paid in")
        self._ensure_allowance(info.pair_asset, t.address, raw)
        tx = self._send(t.functions.addToRewards(raw), "FactoryToken")
        ev = self._expect_event(tx, t, "RewardsDistributed", "FactoryToken", ("distributed", "rewards_per_share"))
        return {
            **tx,
            "distributed": pair_amount(ev["rewardsToBeAdded"], info) if ev else None,
            "rewards_per_share": ev["rewardsPerShareToday"] if ev else None,
        }

    def burn(self, token: str, amount: Union[int, str, float, Decimal]) -> Dict[str, Any]:
        """Burn ``amount`` of a bonded factory token you hold: int = raw 18-decimal wei, str/float/Decimal = human. The
        tokens are gone for good and you get nothing for them; the holders who keep theirs gain from it. Refused before
        any tx for 0 ('cannot be zero'), a main ('FactoryToken'), a token still in its presale ('has not bonded yet') or
        more than you hold ('insufficient balance'). An amount below the minimum burn is refused at simulation ("min
        burn").

        Adds ``burned``: tokens this call took from the signer, 18-decimal wei (its ``Transfer`` out); None with a
        warning when missing."""
        token = to_address(token, "token")
        raw = parse_amount(amount, 18)
        if raw == 0:
            raise ValidationError("cannot be zero", "The burn amount must be above 0.")
        if self.client.mains.is_main(token):
            raise ValidationError("FactoryToken", f"{token} is a main: only factory tokens can be burned.")
        me = self._account
        t = self._token(token)
        if not t.functions.presaleComplete().call():
            raise ValidationError("has not bonded yet", f"Token {token} is still in its presale: only a bonded token can be burned.")
        held = t.functions.balanceOf(me).call()
        if held < raw:
            raise ValidationError("insufficient balance", f"Burning {raw} needs that many tokens; {me} holds {held}.")
        tx = self._send(t.functions.burn(raw), "FactoryToken")
        out = [e for e in self._events(tx, t, "Transfer") if e["from"].lower() == me.lower()]
        if not out:
            add_missing_event_warning(tx, "FactoryToken", "Transfer", ("burned",))
        return {**tx, "burned": sum(e["value"] for e in out) if out else None}

    def get_claimable_rewards(self, token: str, user: Optional[str] = None) -> Amount:
        """Claimable holder rewards of ``user`` (default: the signer), in the pair asset. 0 when rewards are off."""
        token = to_address(token, "token")
        who = self._account if user is None else to_address(user, "user")
        raw = self._token(token).functions.getClaimableRewards(who).call()
        return pair_amount(raw, self._main_info_of(token))

    def get_rewards_info(self, token: str, user: Optional[str] = None) -> Dict[str, Any]:
        """``user``'s (default: the signer) holder-reward state on ``token``. Share amounts are 18-decimal token wei.

        Keys: token, user, rewards_enabled, shares (tokens held, in leverage and in vestings; 0 when excluded or rewards
        off), total_shares, custody_credit (tokens counted for the user while a leverage position holds them),
        vesting_credit (unclaimed tokens in the user's vestings, counted for them; paused while a vesting loan is open),
        excluded (protocol contracts and recycled contracts earn nothing new), claimed (lifetime holder rewards
        claimed, Amount in the pair asset), claimable (Amount), recycle_flagged_at (when the user, a contract,
        was flagged for recycling; 0 = not flagged; a claim clears it), recyclable_at (flagged + 7 days, from then on
        anyone may recycle its unclaimed rewards to all holders; 0 = not flagged), last_distribution (unix seconds).
        """
        token = to_address(token, "token")
        who = self._account if user is None else to_address(user, "user")
        f = self._token(token).functions
        info = self._main_info_of(token)
        flagged = int(f.recycleFlaggedAt(who).call())
        return {
            "token": token,
            "user": who,
            "rewards_enabled": f.rewardsEnabled().call(),
            "shares": f.shares(who).call(),
            "total_shares": f.totalShares().call(),
            "custody_credit": f.custodyCredit(who).call(),
            "vesting_credit": f.vestingCredit(who).call(),
            "excluded": f.isExcluded(who).call(),
            "claimed": pair_amount(f.claimedRewards(who).call(), info),
            "claimable": pair_amount(f.getClaimableRewards(who).call(), info),
            "recycle_flagged_at": flagged,
            "recyclable_at": 0 if flagged == 0 else flagged + RECYCLE_WINDOW,
            "last_distribution": int(f.lastDistribution().call()),
        }


    def get_token_state(self, token: str) -> Dict[str, Any]:
        """Presale, pricing, cash, listing and identity state of a factory token.

        listed (``is_ecosystem_token``): tradable and usable for loans and vesting; False once BASIS delists it
        (``creator_of`` still names its creator). projected_vetted: a display flag BASIS can set on a project; it isn't
        a safety check or a recommendation, and it changes no trading rule. creation_time: unix seconds.
        total_whitelisted: wallets on the presale whitelist, the dev included (starts at 1).

        presale_sold / presale_target / total_supply: 18-decimal token wei. token_price: main per token (unit 'main').
        pair_price: pair asset per token (unit 'pairAsset', scaled by ``pair_unit``). auto_vest / auto_vest_duration
        (whole days) / gradual_auto_vesting: buys are paid out vested. cash_held: pair asset held by the token contract
        (holder rewards not paid out yet, and the like), unit 'pairAsset'.
        """
        token = to_address(token, "token")
        f = self._token(token).functions
        info = self._main_info_of(token)
        cash = self._contract(info.pair_asset, "ERC20").functions.balanceOf(token).call()
        return {
            "token": token,
            "projected_vetted": f.projectedVetted().call(),
            "creation_time": f.creationTime().call(),
            "total_whitelisted": f.totalWhitelisted().call(),
            "listed": self.is_ecosystem_token(token),
            "frozen": f.frozen().call(),
            "presale_complete": f.presaleComplete().call(),
            "presale_sold": f.presaleSold().call(),
            "presale_target": f.presaleTarget().call(),
            "reversed": f.reversed().call(),
            "rewards_enabled": f.rewardsEnabled().call(),
            "hybrid_multiplier": f.hybridMultiplier().call(),
            "auto_vest": f.autoVest().call(),
            "auto_vest_duration": f.autoVestDuration().call(),
            "gradual_auto_vesting": f.gradualAutoVesting().call(),
            "total_supply": f.totalSupply().call(),
            "cash_held": pair_amount(cash, info),
            "token_price": main_amount(f.getTokenPrice().call(), info),
            "pair_price": pair_amount(f.getPairPrice().call(), info),
            "main": info.main,
            "pair_asset": info.pair_asset,
            "dev": f.DEV().call(),
        }

    def get_presale_sold(self, token: str) -> int:
        """Presale tokens sold so far, 18-decimal token wei."""
        return self._token(to_address(token, "token")).functions.presaleSold().call()

    def get_whitelist_status(self, token: str, user: Optional[str] = None) -> Dict[str, Any]:
        """``user``'s (default: the signer) presale whitelist entry on ``token``. Amounts are 18-decimal token wei.

        Keys: whitelisted; max_buy (max uint256 for the dev, 0 when not whitelisted);
        bought (leverage buys count too and may pass the cap);
        remaining (``max_buy - bought``, floored at 0).
        """
        token = to_address(token, "token")
        who = self._account if user is None else to_address(user, "user")
        f = self._token(token).functions
        whitelisted = f.whitelisted(who).call()
        max_buy = f.whitelistMaxBuyForUser(who).call()
        bought = f.whitelistBoughtByUser(who).call()
        return {"whitelisted": whitelisted, "max_buy": max_buy, "bought": bought,
                "remaining": max_buy - bought if max_buy > bought else 0}

    def get_cash_held(self, token_or_main: str) -> Amount:
        """Pair asset held by a factory token or a main, unit 'pairAsset'.
        For a factory token this is pair asset not yet paid out (holder rewards)."""
        addr = to_address(token_or_main, "tokenOrMain")
        info = self.client.mains.get(addr) if self.client.mains.is_main(addr) else self._main_info_of(addr)
        raw = self._contract(info.pair_asset, "ERC20").functions.balanceOf(addr).call()
        return pair_amount(raw, info)

    def get_token_price(self, token: str) -> Amount:
        """Price in main per token (unit 'main', 18 decimals)."""
        token = to_address(token, "token")
        return main_amount(self._token(token).functions.getTokenPrice().call(), self._main_info_of(token))

    def get_pair_price(self, token: str) -> Amount:
        """Price in the main's pair asset per token (unit 'pairAsset', scaled by ``pair_unit``)."""
        token = to_address(token, "token")
        return pair_amount(self._token(token).functions.getPairPrice().call(), self._main_info_of(token))

    def get_floor_price(self, token: str) -> Amount:
        """Floor price per token (unit 'pairAsset'). The floor is measured in the pair asset, which can move itself."""
        token = to_address(token, "token")
        return pair_amount(self._token(token).functions.calculateFloor().call(), self._main_info_of(token))

    def get_token_floor(self, token: str) -> Amount:
        """Floor price per token (unit 'main'). This floor is measured in the main, whose price can move."""
        token = to_address(token, "token")
        return main_amount(self._token(token).functions.calculateTokenFloor().call(), self._main_info_of(token))

    def get_liquidity(self, token_or_main: str) -> Amount:
        """The token's liquidity figure: main tokens for a factory token (unit 'main'), the pair asset for a main (unit
        'pairAsset', scaled by ``pair_unit``). On a factory token it is neither the liquidity added at launch nor the
        main the token holds."""
        addr = to_address(token_or_main, "tokenOrMain")
        if self.client.mains.is_main(addr):
            raw = self._contract(addr, "MainToken").functions.getLiquidity().call()
            return pair_amount(raw, self.client.mains.get(addr))
        return main_amount(self._token(addr).functions.getLiquidity().call(), self._main_info_of(addr))

    def get_total_supply(self, token_or_main: str) -> int:
        """Live ERC-20 ``totalSupply()`` of a factory token or a main, 18-decimal wei."""
        return self._contract(to_address(token_or_main, "tokenOrMain"), "ERC20").functions.totalSupply().call()


    def is_ecosystem_token(self, token: str) -> bool:
        """Listed: tradable and usable for loans and vesting (set at creation; BASIS can delist a token, which turns it
        False). For provenance ("created by this factory") use ``creator_of``."""
        return self._factory.functions.isEcosystemToken(to_address(token, "token")).call()

    def get_tokens_by_creator(self, creator: str) -> List[str]:
        return list(self._factory.functions.getTokensByCreator(to_address(creator, "creator")).call())

    def get_token_count_by_creator(self, creator: str) -> int:
        return self._factory.functions.getTokenCountByCreator(to_address(creator, "creator")).call()

    def creator_of(self, token: str) -> str:
        """Creator of a factory token (zero address when not created by this factory)."""
        return self._factory.functions.creatorOf(to_address(token, "token")).call()

    def get_fee_amount(self) -> int:
        """Creation fee in native wei (before the fee whitelist; see ``get_creation_fee``)."""
        return self._factory.functions.feeAmount().call()

    def is_fee_whitelisted(self, addr: Optional[str] = None) -> bool:
        """True when ``addr`` (default: the signer) creates tokens without a fee."""
        who = self._account if addr is None else to_address(addr, "addr")
        return self._factory.functions.feeWhitelist(who).call()

    def get_pair(self, a: str, b: str) -> str:
        """V2 registry lookup (either order): the pair address, or zero."""
        return self._factory.functions.getPair(to_address(a, "a"), to_address(b, "b")).call()

    def all_pairs_length(self) -> int:
        return self._factory.functions.allPairsLength().call()

    def all_pairs(self, index: int) -> str:
        """Registry entry ``index`` (a whole number below ``all_pairs_length()``)."""
        return self._factory.functions.allPairs(to_uint(index, "index")).call()


    def _main_info_of(self, token: str) -> MainInfo:
        """MainInfo of the main a factory token trades against."""
        return self.client.mains.get(self._token(token).functions.quoteToken().call())
