#!/usr/bin/env python3
"""
Synology File Station sharing-link helper.

This module is intentionally dependency-light so it can run inside the
translator workbench without extra packages.
"""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional
from urllib import parse, request


DEFAULT_ENV_FILES = (
    Path("/home/hoonsw/.openclaw/.env"),
    Path(__file__).with_name(".env"),
)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_values() -> dict[str, str]:
    loaded: dict[str, str] = {}
    for path in DEFAULT_ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in loaded:
                continue
            loaded[key] = _strip_wrapping_quotes(value.strip())
    return loaded


_ENV_FILE_VALUES = _load_env_values()


def _get_setting(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    return _ENV_FILE_VALUES.get(name)


class SynologyShareError(RuntimeError):
    """Raised when Synology sharing cannot be completed."""


@dataclass
class SynologyShareConfig:
    base_url: str
    username: str
    password: str
    mount_root: str = "/mnt/z"
    public_base_url: Optional[str] = None
    share_target: str = "delivered_output"
    share_password: Optional[str] = None
    expire_days: int = 0
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> Optional["SynologyShareConfig"]:
        enabled = _parse_bool(_get_setting("SYNOLOGY_SHARE_ENABLED"), False)
        if not enabled:
            return None
        base_url = _get_setting("SYNOLOGY_BASE_URL")
        username = _get_setting("SYNOLOGY_USERNAME")
        password = _get_setting("SYNOLOGY_PASSWORD")
        if not (base_url and username and password):
            return None
        return cls(
            base_url=base_url.rstrip("/"),
            username=username,
            password=password,
            mount_root=_get_setting("SYNOLOGY_MOUNT_ROOT") or "/mnt/z",
            public_base_url=_get_setting("SYNOLOGY_PUBLIC_BASE_URL"),
            share_target=(_get_setting("SYNOLOGY_SHARE_TARGET") or "delivered_output").strip(),
            share_password=_get_setting("SYNOLOGY_SHARE_PASSWORD") or None,
            expire_days=max(0, _parse_int(_get_setting("SYNOLOGY_SHARE_EXPIRE_DAYS"), 0)),
            verify_ssl=_parse_bool(_get_setting("SYNOLOGY_VERIFY_SSL"), True),
        )


@dataclass
class SynologyShareResult:
    local_path: str
    dsm_path: str
    url: str
    link_id: str


class SynologyShareClient:
    def __init__(self, config: SynologyShareConfig):
        self.config = config
        context = ssl.create_default_context()
        if not config.verify_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._context = context
        self._cookies = CookieJar()
        self._opener = request.build_opener(
            request.HTTPSHandler(context=context),
            request.HTTPCookieProcessor(self._cookies),
        )
        self._logged_in = False

    def __enter__(self) -> "SynologyShareClient":
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()

    def _request_json(self, api_path: str, params: dict[str, str]) -> dict:
        encoded = parse.urlencode(params).encode("utf-8")
        req = request.Request(
            f"{self.config.base_url}{api_path}",
            data=encoded,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self._opener.open(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
        data = json.loads(payload)
        if not data.get("success", False):
            error = data.get("error", {})
            code = error.get("code", "unknown")
            raise SynologyShareError(f"Synology API error code {code}")
        return data.get("data", data)

    def login(self) -> None:
        if self._logged_in:
            return
        self._request_json(
            "/webapi/auth.cgi",
            {
                "api": "SYNO.API.Auth",
                "version": "3",
                "method": "login",
                "account": self.config.username,
                "passwd": self.config.password,
                "session": "FileStation",
                "format": "cookie",
            },
        )
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in:
            return
        try:
            self._request_json(
                "/webapi/auth.cgi",
                {
                    "api": "SYNO.API.Auth",
                    "version": "3",
                    "method": "logout",
                    "session": "FileStation",
                },
            )
        except Exception:
            pass
        self._logged_in = False

    def local_to_dsm_path(self, local_path: str) -> str:
        resolved = Path(local_path).resolve()
        mount_root = Path(self.config.mount_root).resolve()
        try:
            relative = resolved.relative_to(mount_root)
        except ValueError as exc:
            raise SynologyShareError(
                f"Path is outside Synology mount root {mount_root}: {resolved}"
            ) from exc
        return "/" + "/".join(relative.parts)

    def _rewrite_url(self, raw_url: str) -> str:
        public_base = self.config.public_base_url
        if not public_base:
            return raw_url
        raw = parse.urlsplit(raw_url)
        public = parse.urlsplit(public_base.rstrip("/"))
        return parse.urlunsplit(
            (
                public.scheme or raw.scheme,
                public.netloc or raw.netloc,
                raw.path,
                raw.query,
                raw.fragment,
            )
        )

    def create_share_link(self, local_path: str) -> SynologyShareResult:
        dsm_path = self.local_to_dsm_path(local_path)
        params = {
            "api": "SYNO.FileStation.Sharing",
            "version": "3",
            "method": "create",
            "path": json.dumps(dsm_path, ensure_ascii=False),
        }
        if self.config.share_password:
            params["password"] = self.config.share_password
        if self.config.expire_days > 0:
            expires = date.today() + timedelta(days=self.config.expire_days)
            params["date_expired"] = json.dumps(expires.isoformat(), ensure_ascii=False)

        data = self._request_json("/webapi/entry.cgi", params)
        links = data.get("links") or []
        if not links:
            raise SynologyShareError("Synology API returned no sharing link")
        link = links[0]
        if link.get("error", 0) != 0:
            raise SynologyShareError(f"Synology sharing failed with code {link['error']}")
        return SynologyShareResult(
            local_path=str(Path(local_path).resolve()),
            dsm_path=dsm_path,
            url=self._rewrite_url(link["url"]),
            link_id=link["id"],
        )


def create_share_link(local_path: str, config: Optional[SynologyShareConfig] = None) -> SynologyShareResult:
    resolved_config = config or SynologyShareConfig.from_env()
    if resolved_config is None:
        raise SynologyShareError("Synology sharing is not configured")
    with SynologyShareClient(resolved_config) as client:
        return client.create_share_link(local_path)
