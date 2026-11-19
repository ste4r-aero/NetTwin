"""Снимок состояния интерфейсов (REST-версия; живой поток — /ws/interfaces)."""
from fastapi import APIRouter

from ..runtime import runtime
from ..services import netinfo

router = APIRouter()


@router.get("/interfaces")
def interfaces():
    return {
        "interfaces": netinfo.get_interfaces_snapshot(
            dns_cache=runtime.sniffer.iface_dns,
            gw_cache=runtime.sniffer.iface_gw,
        ),
        "stats": runtime.sniffer.stats(),
    }
