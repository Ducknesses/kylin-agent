"""审计链模型 —— 防篡改哈希链审计记录"""

from sqlalchemy import Column, Integer, String, Text, Index

from app.models import Base


class AuditChain(Base):
    """审计链路表 —— 每条记录携带前一条的哈希值形成防篡改链"""

    __tablename__ = "audit_chain"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, nullable=False, index=True)
    timestamp = Column(String, nullable=False, index=True)
    user_input = Column(Text, nullable=False)
    intent = Column(String)
    risk_level = Column(String, nullable=False)
    mcp_tool = Column(String)
    command = Column(Text)
    raw_output = Column(Text)
    llm_reasoning = Column(Text)
    final_response = Column(Text)
    prev_hash = Column(String)
    record_hash = Column(String, nullable=False)
    # 扩展字段（向后兼容旧迁移）
    session_id = Column(String)
    params = Column(Text)
    error = Column(Text)
    event_type = Column(String)

    __table_args__ = (
        Index("idx_trace_id", "trace_id"),
        Index("idx_timestamp", "timestamp"),
    )
