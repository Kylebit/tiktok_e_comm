"""Resolve profit shop scope from the explicitly bound local project registry.

No API, token refresh, credential output, example settings, or global config cache.
Cached identities remain in scope even when their login is unavailable.
"""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3


def _object(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:
        raise ValueError('利润店铺配置不可读取，请修复已绑定项目配置') from exc
    if not isinstance(value,dict):raise ValueError('利润店铺配置结构无效')
    return value


def _path(root,value):
    path=Path(value)
    return path if path.is_absolute() else root/path


def resolve_scope(scope, root):
    root=Path(root).resolve()
    settings=_object(root/'config'/'settings.json')
    platforms=scope.get('platforms') or ['tiktok','shopee']
    sites=scope.get('sites') or ['MY','TH','VN','PH']
    requested=scope.get('shops') or []
    if (not isinstance(platforms,list) or not platforms or set(platforms)-{'tiktok','shopee'}
            or not isinstance(sites,list) or not sites or set(sites)-{'MY','TH','VN','PH'}
            or not isinstance(requested,list) or any(not isinstance(x,str) or not x.strip() for x in requested)):
        raise ValueError('利润平台、站点或店铺范围无效')
    registry={}
    def add(platform,shop,region):
        shop=str(shop or '').strip();region=str(region or '').strip().upper()
        if not shop:raise ValueError('本地店铺记录缺少正式 shop_id，不能自动忽略')
        key=(platform,shop)
        previous=registry.get(key)
        if previous and region and previous!=region:
            raise ValueError('本地店铺站点记录冲突，请先核对配置')
        registry[key]=region or previous or ''
    db=_path(root,settings.get('database') or 'data/shop.db')
    if db.is_file():
        try:
            with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as conn:
                conn.execute('PRAGMA query_only=ON')
                tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for platform,table in [('tiktok','shops'),('shopee','shopee_shops')]:
                    if platform in platforms and table in tables:
                        for shop,region in conn.execute('SELECT shop_id,region FROM '+table):add(platform,shop,region)
        except sqlite3.Error as exc:
            raise ValueError('本地店铺目录读取失败，不能以空店铺范围继续') from exc
    if 'tiktok' in platforms:
        path=_path(root,settings.get('token_file') or 'tiktok_tokens.json')
        if path.is_file():
            rows=_object(path).get('authorized_shops') or []
            if not isinstance(rows,list):raise ValueError('TikTok 授权店铺清单结构无效')
            for row in rows:
                if not isinstance(row,dict):raise ValueError('TikTok 授权店铺记录无效')
                add('tiktok',row.get('id') or row.get('shop_id'),row.get('region'))
    if 'shopee' in platforms:
        config=settings.get('shopee') or {}
        if not isinstance(config,dict):raise ValueError('Shopee 项目配置无效')
        path=_path(root,config.get('token_file') or 'shopee_tokens.json')
        if path.is_file():
            rows=_object(path).get('shops') or {}
            if not isinstance(rows,dict):raise ValueError('Shopee 店铺清单结构无效')
            for key,row in rows.items():
                if not isinstance(row,dict):raise ValueError('Shopee 店铺记录无效')
                if row.get('shop_id') and str(row['shop_id'])!=str(key):raise ValueError('Shopee 店铺身份与索引冲突')
                add('shopee',key,row.get('region'))
    # A missing region may hide a requested store; it must never disappear through filtering.
    if any(not region or region=='?' for (platform,shop),region in registry.items() if not requested or shop in requested):
        raise ValueError('本地店铺有未确定站点，需先核对店铺资料')
    selected={(platform,shop):region for (platform,shop),region in registry.items()
              if platform in platforms and region in sites and (not requested or shop in requested)}
    if requested and {shop for _,shop in selected}!=set(requested):
        raise ValueError('所选店铺不属于已请求的平台与站点，或本地未登记')
    if {(p,r) for (p,_),r in selected.items()}!={(p,r) for p in platforms for r in sites}:
        raise ValueError('请求的平台站点缺少明确店铺登记，不能跳过该店生成完整利润')
    return {**scope,'platforms':sorted(set(platforms)),'sites':sorted(set(sites)),
            'shops':sorted({shop for _,shop in selected})}
