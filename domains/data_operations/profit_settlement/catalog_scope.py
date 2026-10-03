"""Shared catalog identity comparison for captured and legacy consumers."""


def catalog_scope_issue(metadata, *, platform, shop_id, site, currency,
                        product_id="", seller_sku="", variant_id="", require_identity=False):
    identity = metadata.get("identity") or {}
    text = lambda value: str(value).strip() if value is not None else ""
    if not identity:
        return "catalog_scope_unverified" if require_identity else None
    if (
        identity.get("platform") != platform
        or shop_id and text(shop_id) not in {identity.get("shop_key"), metadata.get("provider_shop_id")}
        or metadata.get("region") and text(metadata["region"]).upper() != text(site).upper()
        or metadata.get("currency") and metadata["currency"] != text(currency).upper()
        or product_id and text(product_id) != identity.get("product_id")
        or seller_sku and text(seller_sku) != identity.get("seller_sku")
        or variant_id and text(variant_id) != identity.get("variant_id")
    ):
        return "catalog_scope_mismatch"
    if require_identity and not all((platform, shop_id, site, currency,
                                     metadata.get("region"), metadata.get("currency"))):
        return "catalog_scope_unverified"
    return None
