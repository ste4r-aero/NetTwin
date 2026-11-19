"""Pydantic-модели для REST API."""
from typing import List, Optional

from pydantic import BaseModel, Field

KINDS = ["pc", "printer", "network", "switch", "ap", "camera", "phone", "unknown"]


class NodeCreate(BaseModel):
    label: str = "Новое устройство"
    kind: str = "unknown"
    x: float = 0
    y: float = 0
    color: str = ""
    host_mac: str = ""


class NodePatch(BaseModel):
    label: Optional[str] = None
    kind: Optional[str] = None
    color: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    room_id: Optional[int] = None
    auto_label: Optional[int] = None


class NodePos(BaseModel):
    id: int
    x: float
    y: float
    room_id: Optional[int] = None


class PositionsIn(BaseModel):
    nodes: List[NodePos]


class EdgeCreate(BaseModel):
    src: int
    dst: int
    label: str = ""
    color: str = ""


class EdgePatch(BaseModel):
    label: Optional[str] = None
    color: Optional[str] = None


class RoomCreate(BaseModel):
    name: str = "Новая комната"
    x: float = 0
    y: float = 0
    w: float = 460
    h: float = 340
    color: str = ""


class RoomPatch(BaseModel):
    name: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    color: Optional[str] = None


class HostPatch(BaseModel):
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    note: Optional[str] = None
    ip: Optional[str] = None


class MeasurementIn(BaseModel):
    iface: str = ""
    speed: str = ""
    duplex: str = ""
    ip: str = ""
    gw: str = ""
    dns: str = ""
    note: str = ""


class SessionStart(BaseModel):
    label: str = ""
    iface: str = ""
    duration_sec: int = Field(default=30, ge=5, le=600)


class AgentMac(BaseModel):
    mac: str
    name: str = ""
    desc: str = ""


class AgentPayload(BaseModel):
    hostname: str = ""
    model: str = ""
    serial: str = ""
    cpu: str = ""
    ram_gb: float | int | str | None = None
    ram: str = ""
    os: str = ""
    username: str = ""
    ip: str = ""
    agent: str = "manual"
    macs: List[AgentMac] = []
