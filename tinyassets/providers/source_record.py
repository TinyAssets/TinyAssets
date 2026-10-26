"""The credential-free contract shared by declared and detected model sources.

Declarations are editable JSON files. Mechanisms, never identities, select code.
This module deliberately uses only the standard library so it can be hotfixed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SOURCES = Path(__file__).with_name("source_records")


@dataclass(frozen=True)
class SourceRecord(Mapping):
    id: str
    display_name: str
    origin: str
    addresses: list[str]
    sign_in: dict
    invoke: dict
    egress: dict
    state: dict
    suggested: bool = False
    execution: dict | None = None
    models: list = field(default_factory=list)
    ranking: dict | None = None

    def document(self):
        return asdict(self)

    def digest_document(self):
        """The pre-placement shape that live CLI connection markers were digested over.

        ``execution`` replaced a ``free`` flag; a changed digest would make every
        connected CLI source ask its owner to reconnect.
        """
        document = self.document()
        execution = document.pop("execution")
        document["free"] = "free" in execution["billing"]
        return document

    @property
    def acquisition_document(self):
        """Wire adapter for pending PKCE key flows created by the live image."""
        oauth, http = self.sign_in["oauth"], self.invoke["http"]
        return {"protocol": oauth["protocol"], "display_name": self.display_name,
                "manual_key_entry": oauth.get("manual_key_entry", False),
                "authorize_url": oauth["authorization_endpoint"],
                "authorize_params": oauth.get("authorize_params", {}),
                "exchange_url": oauth["token_endpoint"], "manage_url": oauth["help_url"],
                "billing_url": http["billing_url"], "inference_url": http["inference_url"],
                "catalogue_url": http["models_url"], "benchmark_url": http["benchmark_url"]}

    @property
    def digest(self):
        # Preserve live pending-flow digests while changing only the source representation.
        data = [self.acquisition_document, self.invoke["http"]["discovery"]]
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
                              ).hexdigest()

    @property
    def authorize_url(self):
        return self.sign_in["oauth"]["authorization_endpoint"]

    @property
    def exchange_url(self):
        return self.sign_in["oauth"]["token_endpoint"]

    @property
    def authorize_params(self):
        return tuple(sorted(self.sign_in["oauth"].get("authorize_params", {}).items()))

    @property
    def manage_url(self):
        return self.sign_in["oauth"]["help_url"]

    @property
    def inference_url(self):
        return self.invoke["http"]["inference_url"]

    @property
    def catalogue_url(self):
        return self.invoke["http"]["models_url"]

    @property
    def benchmark_url(self):
        return self.invoke["http"]["benchmark_url"]

    def __getitem__(self, key):
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(self.__dataclass_fields__)

    def __len__(self):
        return len(self.__dataclass_fields__)

    @classmethod
    def parse(cls, doc):
        from tinyassets.connection_oauth.transport import validate_https_url
        from tinyassets.providers.placement import validate

        doc = dict(doc)
        free = doc.pop("free", False)  # The pre-placement flag, read once.
        if doc.get("execution") is None:
            # A record without the block runs where its mechanism always ran:
            # a CLI in the owner's box on its plan, HTTP on the thin loop.
            cli = "cli_login" in doc.get("sign_in", {})
            doc["execution"] = {"placement": ["hosted_box" if cli else "thin_loop"],
                                "billing": ["free" if free is True
                                            else "plan" if cli else "metered"],
                                "serves": "owner_only"}
        record = cls(**doc)
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", record.id)
                or not record.display_name.strip() or record.origin not in {"declared", "detected"}
                or type(record.suggested) is not bool
                or not isinstance(record.addresses, list) or not record.addresses):
            raise ValueError("invalid source identity")
        validate(record.execution, cli="cli" in record.invoke)
        for address in record.addresses:
            validate_https_url(address)
        if len(record.sign_in) != 1 or len(record.invoke) != 1:
            raise ValueError("source requires one sign-in and invocation mechanism")
        if not set(record.sign_in) <= {"oauth", "cli_login", "api_key"}:
            raise ValueError("unknown source sign-in")
        if not set(record.invoke) <= {"http", "cli"}:
            raise ValueError("unknown source invocation")
        if not record.egress or any(
                not isinstance(hosts, list) or not hosts
                or any(not isinstance(h, str) or not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", h) or ".." in h for h in hosts)
                for hosts in record.egress.values()):
            raise ValueError("source egress requires exact hosts")
        if "cli_login" in record.sign_in:
            if set(record.invoke) != {"cli"} or record.state.get("location") != "broker":
                raise ValueError("CLI credentials require broker custody")
            from tinyassets.providers.subscription_source import validate_cli

            login = record.sign_in["cli_login"]
            validate_cli({"id": record.id, "display_name": record.display_name,
                "install": login["install"], "login": login["login"],
                "invoke": record.invoke["cli"], "egress": record.egress,
                "state_paths": record.state["paths"], "env": login.get("env", {}),
                "files": login.get("files", {}),
                **({"models": record.models} if record.models else {}),
                **({"ranking": record.ranking} if record.ranking else {})})
        else:
            if set(record.invoke) != {"http"} or record.state != {"location": "broker"}:
                raise ValueError("HTTP credentials require broker custody")
            http = record.invoke["http"]
            if http["wire"] != "chat_completions":
                raise ValueError("unsupported model wire")
            hosts = {h for group in record.egress.values() for h in group}
            urls = [http[k] for k in ("base_url", "models_url", "inference_url")]
            oauth = record.sign_in.get("oauth")
            if oauth:
                if oauth["client_strategy"] not in {"dcr", "metadata_document", "public_client"}:
                    raise ValueError("unknown OAuth client strategy")
                if oauth["pkce"] != "S256":
                    raise ValueError("source OAuth requires PKCE S256")
                urls += [oauth[k] for k in ("issuer", "authorization_endpoint", "token_endpoint")]
                if oauth.get("registration_endpoint"):
                    urls.append(oauth["registration_endpoint"])
            elif record.sign_in["api_key"]["header_style"] != "bearer":
                raise ValueError("unsupported API key header style")
            for url in urls:
                allow_query = url in [http["models_url"], http["inference_url"]]
                if urlsplit(validate_https_url(url, allow_query=allow_query)).hostname not in hosts:
                    raise ValueError("source endpoint outside declared egress")
            if oauth:
                from tinyassets.connection_oauth.site_identity import site

                if len({site(url) for url in urls}) != 1:
                    raise ValueError("source authorization must share the model API site")
        return record


def records(home: Path | None = None, *, strict=False):
    from tinyassets.connection_oauth.transport import OAuthError

    result = {}
    for directory in [SOURCES, *([home / ".ta-cli" / "sources"] if home else [])]:
        for path in sorted(directory.glob("*.json")):
            try:
                document = json.loads(path.read_text("utf-8"))
                # One-shot legacy source layout bridge; execution only uses broker custody.
                if ("cli_login" in document.get("sign_in", {})
                        and document.get("state", {}).get("location") == "owner_box"):
                    document["state"]["location"] = "broker"
                record = SourceRecord.parse(document)
                if record.origin != "declared" or path.stem != record.id:
                    raise ValueError("declared source filename must match its id")
            except (ValueError, TypeError, KeyError, AttributeError, OSError, OAuthError):
                if strict:
                    raise  # A broken shipped record must fail the image build.
                logging.getLogger(__name__).warning(
                    "Ignoring malformed source record: %s", path.name)
                # A malformed override must not silently reactivate the built-in source.
                result.pop(path.stem, None)
                continue
            result[record.id] = record
    return result


def resolve(address, home=None):
    """Exact origin/path aliases; never substring or suffix-match an authority."""
    from tinyassets.connection_oauth.model_discovery import address_url

    normalized = address_url(address)
    matches = [record for record in records(home).values()
               if normalized in {address_url(alias) for alias in record.addresses}]
    if len(matches) > 1:
        raise ValueError("ambiguous declared source address")
    return matches[0] if matches else None


def suggested_address():
    choices = [r for r in records().values() if r.suggested and "free" in r.execution["billing"]]
    if len(choices) != 1:
        raise ValueError("one suggested free source required")
    return choices[0].addresses[0]
