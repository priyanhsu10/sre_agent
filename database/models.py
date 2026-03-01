"""
Database models for RCA reports storage.

Author: Alex (ARCHITECT) - Dashboard Enhancement
"""

from datetime import datetime
from typing import Optional, List
import json
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, Boolean,
    ForeignKey, JSON, create_engine, Index
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Session

Base = declarative_base()


class Investigation(Base):
    """Main investigation record"""
    __tablename__ = 'investigations'

    id = Column(String, primary_key=True)  # investigation_id
    app_name = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, index=True)
    environment = Column(String, nullable=False, index=True)
    alert_time = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Classification results
    root_cause_category = Column(String, nullable=False, index=True)
    confidence_level = Column(String, nullable=False)
    confidence_percentage = Column(Float)
    is_code_change = Column(Boolean, default=False, index=True)

    # Status
    status = Column(String, default='completed', index=True)  # pending, in_progress, completed, failed
    duration_ms = Column(Float)

    # Relationships
    errors = relationship("InvestigationError", back_populates="investigation", cascade="all, delete-orphan")
    hypotheses = relationship("Hypothesis", back_populates="investigation", cascade="all, delete-orphan")
    steps = relationship("InvestigationStep", back_populates="investigation", cascade="all, delete-orphan")
    code_changes = relationship("CodeChange", back_populates="investigation", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_app_time', 'app_name', 'alert_time'),
        Index('idx_severity_env', 'severity', 'environment'),
    )


class InvestigationError(Base):
    """Errors that triggered the investigation"""
    __tablename__ = 'investigation_errors'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False)
    correlation_id = Column(String, index=True)
    error_message = Column(Text, nullable=False)

    investigation = relationship("Investigation", back_populates="errors")


class Hypothesis(Base):
    """Classification hypotheses"""
    __tablename__ = 'hypotheses'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False)
    category = Column(String, nullable=False)
    confidence_percentage = Column(Float, nullable=False)
    confidence_level = Column(String, nullable=False)
    reasoning = Column(Text)
    rank = Column(Integer)  # 1, 2, 3 for top 3

    investigation = relationship("Investigation", back_populates="hypotheses")


class InvestigationStep(Base):
    """Investigation steps and reasoning"""
    __tablename__ = 'investigation_steps'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False)
    step_number = Column(Integer, nullable=False)
    reasoning = Column(Text, nullable=False)
    decision = Column(Text, nullable=False)
    tool_called = Column(String)  # loki, git_blame, jira, or null
    result_summary = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

    investigation = relationship("Investigation", back_populates="steps")


class CodeChange(Base):
    """Git commits related to investigation"""
    __tablename__ = 'code_changes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False)
    commit_hash = Column(String, nullable=False)
    author = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    message = Column(Text, nullable=False)
    files_changed = Column(JSON)  # List of files
    jira_ticket = Column(String, index=True)
    risk_flags = Column(JSON)  # List of risk indicators

    investigation = relationship("Investigation", back_populates="code_changes")


class LogEvidence(Base):
    """Log evidence from Loki"""
    __tablename__ = 'log_evidence'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False, unique=True)
    correlation_id = Column(String)
    evidence_path = Column(String)
    stack_traces = Column(JSON)  # List of stack traces
    key_log_lines = Column(JSON)  # List of log lines
    slow_queries = Column(JSON)  # List of slow queries
    total_error_count = Column(Integer, default=0)


class JiraTicket(Base):
    """Jira tickets related to investigation"""
    __tablename__ = 'jira_tickets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    investigation_id = Column(String, ForeignKey('investigations.id'), nullable=False)
    ticket_key = Column(String, nullable=False, index=True)
    summary = Column(Text)
    status = Column(String)
    assignee = Column(String, index=True)
    labels = Column(JSON)
    risk_flags = Column(JSON)


# Database helper functions
def init_database(db_url: str = "sqlite:///./reports.db"):
    """Initialize database and create all tables"""
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get database session"""
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
