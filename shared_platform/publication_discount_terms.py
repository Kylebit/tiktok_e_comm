"""Explicit product action terms, separate from original pricing reserves."""
from decimal import Decimal,InvalidOperation

KEY='publication_discounts_by_target'
QUOTE='postpublish_discount'


def source_terms(state):
    from shared_platform.postpublish_promotions import promotion_action_target,promotion_target_policy
    supplied=[]
    for name in ('source','review'):
        value=(state.get(name) or {}).get(KEY)
        if value is None:continue
        if type(value) is not dict or not value:raise ValueError('EXPLICIT_DISCOUNT_TERMS_INVALID')
        checked={}
        for label,percent in value.items():
            promotion_target_policy(promotion_action_target(label))
            if type(percent) is not int or not 0<percent<100:raise ValueError('EXPLICIT_DISCOUNT_PERCENT_INVALID')
            checked[label]=percent
        supplied.append(checked)
    if not supplied:return None
    if any(value!=supplied[0] for value in supplied[1:]):raise ValueError('EXPLICIT_DISCOUNT_SOURCE_REVIEW_CONFLICT')
    return supplied[0]


def _decimal(value):
    if isinstance(value,bool):raise ValueError('EXPLICIT_DISCOUNT_NUMBER_INVALID')
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('EXPLICIT_DISCOUNT_NUMBER_INVALID') from None
    if not number.is_finite() or number<=0:raise ValueError('EXPLICIT_DISCOUNT_NUMBER_INVALID')
    return number


def _reserve(quote):
    values=[quote.get('discount_reserve_pct'),
        (quote.get('formula_parameters') or {}).get('discount_reserve_pct'),
        (quote.get('header_meta') or {}).get('discount_reserve_pct')]
    values=[_decimal(v) for v in values if v is not None]
    if len(set(values))>1:raise ValueError('EXPLICIT_DISCOUNT_PROTECTION_SOURCE_CONFLICT')
    return values[0] if values else None


def frozen_quote(value,*,amount,currency):
    if (type(value) is not dict or set(value)!={'schema_version','percent_off','list_price','sale_price','currency','source'}
            or value.get('schema_version')!='explicit-product-discount/v1'
            or value.get('source')!='EXPLICIT_PRODUCT_REVIEW'
            or type(currency) is not str or not currency
            or type(value.get('percent_off')) is not int or not 0<value['percent_off']<100
            or value.get('currency')!=currency or _decimal(value.get('list_price'))!=_decimal(amount)
            or _decimal(value.get('sale_price'))!=_decimal(amount)*(1-Decimal(value['percent_off'])/100)):
        raise ValueError('EXPLICIT_DISCOUNT_FROZEN_QUOTE_CONFLICT')
    return {'currency':currency,'list_price':amount,'discount_reserve_pct':value['percent_off'],
            'sale_after_discount':value['sale_price']}


def apply_source_terms(pricing,state,selected_targets):
    """Add only explicit terms; keep every original quote/calculation intact."""
    from shared_platform.postpublish_promotions import promotion_action_target,promotion_target_policy
    terms=source_terms(state)
    if terms is None:return
    if list(selected_targets)==['miaoshou:COMMON']:
        return # Technical draft stage has no marketplace discount actions.
    if set(terms)-set(selected_targets):raise ValueError('EXPLICIT_DISCOUNT_TARGET_NOT_SELECTED')
    for label,percent in terms.items():
        row=(pricing.get('target_pricing') or {}).get(label)
        if not isinstance(row,dict) or not row.get('sku_prices'):raise ValueError('EXPLICIT_DISCOUNT_PRICE_SOURCE_MISSING')
        expected=promotion_target_policy(promotion_action_target(label))['currency']
        for quote in row['sku_prices']:
            source=quote.get('source') or {};derived=quote.get('derived_preview') or {}
            amount=quote.get('list_price',derived.get('local_original_price',source.get('list_price')))
            currency=quote.get('currency',derived.get('source_currency',source.get('currency')))
            if currency!=expected:raise ValueError('EXPLICIT_DISCOUNT_CURRENCY_CONFLICT')
            # Prove this action is no deeper than the actual source calculator's
            # reserve. Never reuse minimum-profit results for a deeper discount.
            key=quote.get('target_key') or source.get('target_key') or (row.get('source') or {}).get('target_key') or (row.get('store_target') or {}).get('target_key')
            variant=next((v for v in pricing.get('sku_pricing') or [] if v.get('variant_key')==quote.get('variant_key')),None)
            originals=list((variant or {}).get('selected_store_prices') or pricing.get('selected_store_prices') or [])
            retained=[quote]+[p for p in originals if key is not None and p.get('target_key')==key]
            # An inherited A-link price replaces the calculator's list price
            # without repricing its reserve/profit result. That old percentage
            # cannot protect a new action at the replacement amount.
            if any('price_override' in p or p.get('pricing_authority')
                    for p in retained+[source]):
                raise ValueError('EXPLICIT_DISCOUNT_OVERRIDDEN_PRICE_UNPROTECTED')
            reserves=[_reserve(p) for p in retained]
            reserves=[_decimal(p) for p in reserves if p is not None]
            if not reserves or len(set(reserves))!=1:raise ValueError('EXPLICIT_DISCOUNT_PROTECTION_SOURCE_MISSING:'+label)
            if Decimal(percent)>reserves[0]:raise ValueError('EXPLICIT_DISCOUNT_EXCEEDS_PRICED_RESERVE')
            price=_decimal(amount);sale=price*(1-Decimal(percent)/100)
            quote[QUOTE]={'schema_version':'explicit-product-discount/v1','percent_off':percent,
                'list_price':format(price,'f'),'sale_price':format(sale,'f'),'currency':currency,
                'source':'EXPLICIT_PRODUCT_REVIEW'}
