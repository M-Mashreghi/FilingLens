"""Direct filing inline-XBRL fact extractor (``ixbrl-extractor/1``).

Extracts every ``ix:nonFraction`` / ``ix:nonNumeric`` fact of one filing with its context
(period, dimensions), unit, decimals/scale/sign, standard-vs-custom concept identity,
taxonomy namespace, hidden/visible status and a DOM locator. Dimensional facts keep their
axis/member pairs; nothing is flattened into entity-wide values here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree

from filinglens.domain.xbrl import FactSource, XbrlFact

EXTRACTOR_VERSION = "ixbrl-extractor/1"
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


@dataclass(frozen=True)
class Context:
    context_id: str
    start: date | None
    end: date | None
    instant: bool
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class IxbrlExtraction:
    facts: tuple[XbrlFact, ...]
    contexts: dict[str, Context]
    units: dict[str, str]
    namespaces: dict[str, str]
    errors: tuple[str, ...] = field(default_factory=tuple)
    version: str = EXTRACTOR_VERSION


def _local(tag: Any) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def _strip_prefix(qname: str) -> str:
    return qname.split(":", 1)[1] if ":" in qname else qname


def _parse_contexts(root: Any) -> dict[str, Context]:
    out: dict[str, Context] = {}
    for ctx in root.iter(f"{{{XBRLI_NS}}}context"):
        cid = ctx.get("id")
        start = end = None
        instant = False
        period = ctx.find(f"{{{XBRLI_NS}}}period")
        if period is not None:
            inst = period.find(f"{{{XBRLI_NS}}}instant")
            if inst is not None and inst.text:
                end, instant = date.fromisoformat(inst.text.strip()[:10]), True
            else:
                s = period.find(f"{{{XBRLI_NS}}}startDate")
                e = period.find(f"{{{XBRLI_NS}}}endDate")
                start = (
                    date.fromisoformat(s.text.strip()[:10]) if s is not None and s.text else None
                )
                end = date.fromisoformat(e.text.strip()[:10]) if e is not None and e.text else None
        dims: list[tuple[str, str]] = []
        for m in ctx.iter(f"{{{XBRLDI_NS}}}explicitMember"):
            dims.append((m.get("dimension", ""), (m.text or "").strip()))
        for m in ctx.iter(f"{{{XBRLDI_NS}}}typedMember"):
            dims.append((m.get("dimension", ""), "typed:" + "".join(m.itertext()).strip()))
        out[cid] = Context(cid, start, end, instant, tuple(sorted(dims)))
    return out


def _parse_units(root: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for unit in root.iter(f"{{{XBRLI_NS}}}unit"):
        uid = unit.get("id")
        divide = unit.find(f"{{{XBRLI_NS}}}divide")
        if divide is not None:
            num = [
                _strip_prefix((m.text or "").strip())
                for m in divide.find(f"{{{XBRLI_NS}}}unitNumerator").iter(f"{{{XBRLI_NS}}}measure")
            ]
            den = [
                _strip_prefix((m.text or "").strip())
                for m in divide.find(f"{{{XBRLI_NS}}}unitDenominator").iter(
                    f"{{{XBRLI_NS}}}measure"
                )
            ]
            out[uid] = "*".join(num) + "/" + "*".join(den)
        else:
            measures = [
                _strip_prefix((m.text or "").strip()) for m in unit.iter(f"{{{XBRLI_NS}}}measure")
            ]
            out[uid] = "*".join(measures)
    return out


def parse_ix_number(text: str, fmt: str | None) -> Decimal:
    """Parse a displayed iXBRL number according to common ixt formats."""
    t = text.strip()
    f = (fmt or "").lower()
    if "fixed-zero" in f or "zerodash" in f or t in ("-", "—", "–"):
        return Decimal(0)
    if "comma-decimal" in f or "numcommadecimal" in f:
        t = t.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace(" ", "")
    t = t.strip("()$€£")
    try:
        return Decimal(t)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable iXBRL number {text!r} (format {fmt})") from exc


def _hidden(el: Any) -> bool:
    for anc in el.iterancestors():
        if anc.tag == f"{{{IX_NS}}}hidden":
            return True
        style = (anc.get("style") or "").replace(" ", "").lower()
        if "display:none" in style and anc.tag != f"{{{IX_NS}}}header":
            return True
    return False


def extract_ixbrl(raw: bytes, *, cik: int, accession: str) -> IxbrlExtraction:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(raw, parser=parser)
    if root is None:
        return IxbrlExtraction((), {}, {}, {}, ("document could not be parsed",))
    tree = root.getroottree()
    contexts = _parse_contexts(root)
    units = _parse_units(root)
    namespaces: dict[str, str] = {}
    errors: list[str] = []
    facts: list[XbrlFact] = []
    for el in root.iter(f"{{{IX_NS}}}nonFraction", f"{{{IX_NS}}}nonNumeric"):
        name = el.get("name") or ""
        prefix, _, concept = name.partition(":")
        if not concept:
            errors.append(f"fact without prefixed name at {tree.getelementpath(el)}")
            continue
        ns = el.nsmap.get(prefix)
        if ns:
            namespaces[prefix] = ns
        ctx = contexts.get(el.get("contextRef") or "")
        if ctx is None:
            errors.append(f"{name}: unknown context {el.get('contextRef')!r}")
            continue
        numeric = el.tag == f"{{{IX_NS}}}nonFraction"
        value: Decimal | None = None
        text_value: str | None = None
        scale = int(el.get("scale")) if el.get("scale") not in (None, "") else None
        negated = el.get("sign") == "-"
        if numeric:
            if el.get(XSI_NIL) == "true":
                value = None
            else:
                try:
                    value = parse_ix_number("".join(el.itertext()), el.get("format"))
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if scale:
                    value = value.scaleb(scale)
                if negated:
                    value = -value
        else:
            text_value = " ".join("".join(el.itertext()).split())
        unit = units.get(el.get("unitRef") or "") if numeric else None
        if numeric and el.get("unitRef") and unit is None:
            errors.append(f"{name}: unknown unit {el.get('unitRef')!r}")
        facts.append(
            XbrlFact(
                source=FactSource.IXBRL,
                cik=cik,
                accession=accession,
                concept=concept,
                prefix=prefix,
                namespace=ns,
                unit=unit,
                value=value,
                text_value=text_value,
                period_start=None if ctx.instant else ctx.start,
                period_end=ctx.end,
                is_instant=ctx.instant,
                decimals=el.get("decimals"),
                scale=scale,
                sign_negated=negated,
                dimensions=ctx.dimensions,
                context_id=ctx.context_id,
                hidden=_hidden(el),
                locator=tree.getelementpath(el),
            )
        )
    return IxbrlExtraction(tuple(facts), contexts, units, namespaces, tuple(errors))
