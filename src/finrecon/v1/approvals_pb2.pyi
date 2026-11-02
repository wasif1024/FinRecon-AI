from finrecon.v1 import cases_pb2 as _cases_pb2
from finrecon.v1 import enums_pb2 as _enums_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PendingApproval(_message.Message):
    __slots__ = ("request", "case_number", "case_title", "recommendation")
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    CASE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    CASE_TITLE_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDATION_FIELD_NUMBER: _ClassVar[int]
    request: _cases_pb2.ApprovalRequest
    case_number: str
    case_title: str
    recommendation: _cases_pb2.Recommendation
    def __init__(self, request: _Optional[_Union[_cases_pb2.ApprovalRequest, _Mapping]] = ..., case_number: _Optional[str] = ..., case_title: _Optional[str] = ..., recommendation: _Optional[_Union[_cases_pb2.Recommendation, _Mapping]] = ...) -> None: ...

class ListPendingApprovalsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListPendingApprovalsResponse(_message.Message):
    __slots__ = ("approvals",)
    APPROVALS_FIELD_NUMBER: _ClassVar[int]
    approvals: _containers.RepeatedCompositeFieldContainer[PendingApproval]
    def __init__(self, approvals: _Optional[_Iterable[_Union[PendingApproval, _Mapping]]] = ...) -> None: ...

class EvidenceSelection(_message.Message):
    __slots__ = ("evidence_ids",)
    EVIDENCE_IDS_FIELD_NUMBER: _ClassVar[int]
    evidence_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, evidence_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class DecideApprovalRequest(_message.Message):
    __slots__ = ("request_id", "decision", "comment", "evidence_considered")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    COMMENT_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_CONSIDERED_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    decision: _enums_pb2.ApprovalDecisionType
    comment: str
    evidence_considered: EvidenceSelection
    def __init__(self, request_id: _Optional[str] = ..., decision: _Optional[_Union[_enums_pb2.ApprovalDecisionType, str]] = ..., comment: _Optional[str] = ..., evidence_considered: _Optional[_Union[EvidenceSelection, _Mapping]] = ...) -> None: ...

class DecideApprovalResponse(_message.Message):
    __slots__ = ("decision",)
    DECISION_FIELD_NUMBER: _ClassVar[int]
    decision: _cases_pb2.Decision
    def __init__(self, decision: _Optional[_Union[_cases_pb2.Decision, _Mapping]] = ...) -> None: ...
