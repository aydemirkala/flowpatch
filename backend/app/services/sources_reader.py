from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
from sqlalchemy.orm import Session

from ..config import settings


@dataclass(frozen=True)
class SourceItem:
    platform: str
    namespace: str
    resource_name: str
    kind: str
    product_name: str | None = None


def read_sources(file_path: str | None = None, db: Session | None = None) -> List[SourceItem]:
    path = file_path or settings.sources_file
    items: list[SourceItem] = []
    
    # Import here to avoid circular dependency
    if db:
        from .exclusions import should_skip_namespace
    
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) not in (4, 5):
                continue
            if parts[0].lower() == "platform":
                continue
            
            # Skip excluded namespaces
            namespace = parts[1] if len(parts) >= 2 else ""
            if db and should_skip_namespace(namespace, db):
                continue
            
            known_kinds = {"deployment","statefulset","daemonset","job","cronjob","pod"}
            # Defaults for legacy 4-column layout
            resource_name = parts[2] if len(parts) >= 3 else ""
            kind = parts[3] if len(parts) >= 4 else "deployment"
            product = None
            if len(parts) == 5:
                # Support new order: PLATFORM,NAMESPACE,PRODUCT_NAME,RESOURCE_NAME,KIND
                new_kind = parts[4]
                new_product = parts[2]
                new_resource = parts[3]
                if new_kind.lower() in known_kinds:
                    product = new_product or None
                    resource_name = new_resource
                    kind = new_kind
                else:
                    # Legacy order: PLATFORM,NAMESPACE,RESOURCE_NAME,KIND,PRODUCT_NAME
                    legacy_kind = parts[3]
                    legacy_product = parts[4]
                    if legacy_kind.lower() in known_kinds:
                        product = legacy_product or None
                        resource_name = parts[2]
                        kind = legacy_kind
                    else:
                        # Fallback: try swap heuristic between cols 3 and 4
                        product = parts[3]
                        kind = parts[4]
                        resource_name = parts[2]
            items.append(SourceItem(platform=parts[0], namespace=parts[1], resource_name=resource_name, kind=kind, product_name=product))
    return items


def iter_in_batches(elements: Iterable[SourceItem], batch_size: int = 50) -> Iterable[list[SourceItem]]:
    batch: list[SourceItem] = []
    for el in elements:
        batch.append(el)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
