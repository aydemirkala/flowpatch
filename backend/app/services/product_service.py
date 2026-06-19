"""
Service for managing product list from endoflife.date API
"""
import urllib.request
import urllib.error
import json
from typing import List
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from ..models import ProductList
from ..logging_utils import log_event


def fetch_product_list_from_api() -> List[str]:
    """
    Fetch full product list from endoflife.date API
    Returns list of product names
    
    API format: {"result": [{"name": "alpine", ...}, {"name": "amazon-linux", ...}, ...]}
    """
    url = "https://endoflife.date/api/v1/products/full"
    
    try:
        log_event("product_list.fetch.start", url=url)
        
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "PatchManagement/1.0")
        
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec - controlled GET
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            raw_data = resp.read().decode("utf-8")
            data = json.loads(raw_data)
            
            # Extract product names from result[].name
            product_names = []
            
            if isinstance(data, dict) and "result" in data:
                result = data["result"]
                
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict) and "name" in item:
                            product_names.append(item["name"])
                else:
                    raise Exception(f"Expected 'result' to be a list, got {type(result).__name__}")
            else:
                # Fallback: maybe it's just a list of strings
                if isinstance(data, list):
                    product_names = [item for item in data if isinstance(item, str)]
                else:
                    raise Exception(f"Expected dict with 'result' key or list, got {type(data).__name__}")
            
            log_event("product_list.fetch.success", count=len(product_names))
            return product_names
            
    except urllib.error.HTTPError as e:
        log_event("product_list.fetch.error", status=e.code, error=str(e))
        raise Exception(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        log_event("product_list.fetch.error", error=str(e))
        raise Exception(f"URL Error: {e.reason}")
    except json.JSONDecodeError as e:
        log_event("product_list.fetch.json_error", error=str(e))
        raise Exception(f"JSON decode error: {str(e)}")
    except Exception as e:
        log_event("product_list.fetch.error", error=str(e), error_type=type(e).__name__)
        raise


def refresh_product_list(db: Session) -> dict:
    """
    Refresh product list in database from API
    Returns dict with statistics
    """
    try:
        # Fetch from API
        product_names = fetch_product_list_from_api()
        
        if not product_names:
            return {"ok": False, "error": "No products fetched from API"}
        
        # Get existing products
        existing_products = {p.product_name for p in db.query(ProductList).all()}
        
        added = 0
        updated = 0
        
        # Add or update products
        for name in product_names:
            if name in existing_products:
                # Update timestamp
                product = db.query(ProductList).filter(ProductList.product_name == name).first()
                if product:
                    product.updated_at = datetime.now(timezone.utc)
                    updated += 1
            else:
                # Add new product
                product = ProductList(product_name=name)
                db.add(product)
                added += 1
        
        db.commit()
        
        log_event(
            "product_list.refresh.success",
            total=len(product_names),
            added=added,
            updated=updated
        )
        
        return {
            "ok": True,
            "total": len(product_names),
            "added": added,
            "updated": updated,
            "message": f"Refreshed product list: {added} added, {updated} updated"
        }
        
    except Exception as e:
        db.rollback()
        log_event("product_list.refresh.error", error=str(e))
        return {"ok": False, "error": str(e)}

