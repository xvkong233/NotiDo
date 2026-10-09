from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from notido.dates import normalize, overdue
from notido.errors import NotiDoError
from notido.keys import key
from notido.models import PLAN_ADAPTER, Patch, Settings, Target
from notido.policy import managed_notes, replace_managed, resolve_project, verify_evidence


def date_value(text, time=None, kind="date_only", anchor=datetime(2026, 10, 9, 8, tzinfo=UTC)):
    return normalize(
        text, time, anchor=anchor, timezone="Asia/Shanghai", kind=kind, evidence_id="e"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("今天", "2026-10-09"),
        ("明天", "2026-10-10"),
        ("后天", "2026-10-11"),
        ("周五", "2026-10-09"),
        ("下周一", "2026-10-12"),
        ("下周日", "2026-10-18"),
    ],
)
def test_relative_fixed_anchor(text, expected):
    assert date_value(text)["local_date"] == expected


def test_midnight_and_sunday():
    assert (
        date_value("明天", anchor=datetime(2026, 12, 31, 16, tzinfo=UTC))["local_date"]
        == "2027-01-02"
    )
    assert (
        date_value("下周一", anchor=datetime(2026, 10, 11, 8, tzinfo=UTC))["local_date"]
        == "2026-10-12"
    )


def test_all_day_no_invented_time():
    value = date_value("2026年10月9日")
    assert value["is_all_day"] and value["instant"] is None and value["local_time"] is None
    assert not overdue(value, datetime(2026, 10, 9, 15, 59, tzinfo=UTC))
    assert overdue(value, datetime(2026, 10, 9, 16, tzinfo=UTC))


def test_2400():
    value = date_value("2026-12-31", "24:00", "timed")
    assert value["local_date"] == "2027-01-01" and value["local_time"] == "00:00"
    assert "24:00" in value["raw_text"]


def test_inline_time_cannot_be_silently_discarded_from_date_field():
    with pytest.raises(NotiDoError, match="明确时刻"):
        date_value("2027年12月24日24:00截止")


@pytest.mark.parametrize(
    "time_text", ["2027年12月24日17:40", "2027-12-24T17:40", "2027/12/24 下午5点40分"]
)
def test_full_datetime_time_field_requires_matching_date(time_text):
    value = date_value("2027年12月24日", time_text, "timed")
    assert value["local_date"] == "2027-12-24" and value["local_time"] == "17:40"
    assert time_text in value["raw_text"]


@pytest.mark.parametrize(
    "time_text",
    [
        "2027年12月25日17:40",
        "2027年12月24日17:40或18:30",
        "2027年12月24日25:00",
        "2027年2月30日17:40",
    ],
)
def test_conflicting_or_ambiguous_full_datetime_stays_blocked(time_text):
    with pytest.raises(NotiDoError):
        date_value("2027年12月24日", time_text, "timed")


@pytest.mark.parametrize("text", ["12月31日", "第三教学周", "2026-02-30", "2026年10月9日前"])
def test_ambiguous_or_invalid_date_blocks(text):
    with pytest.raises(NotiDoError):
        date_value(text)


def test_forward_unknown_anchor():
    with pytest.raises(NotiDoError, match="原通知"):
        date_value("明天", anchor=None)
    assert date_value("2027-01-02", anchor=None)["local_date"] == "2027-01-02"


def test_notes_do_not_claim_receipt_time_is_original_publication_date():
    absolute = date_value("2027-12-21", "17:00", "timed")
    assert absolute["anchor_basis"] is None
    notes = managed_notes("n", [], [], absolute)
    assert "已知原日期" not in notes and "未使用相对日期" in notes
    anchor = datetime(2027, 12, 20, 2, tzinfo=UTC)
    relative = date_value("明天", "17:00", "timed", anchor=anchor)
    assert relative["anchor_basis"] == anchor.isoformat()
    assert f"日期解析锚点：{anchor.isoformat()}" in managed_notes("n", [], [], relative)
    assert relative["local_date"] == "2027-12-21"


def test_strict_dto_and_discriminated_intents():
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python(
            {
                "schema_version": 4,
                "intent": "complete",
                "ambiguities": [],
                "target": {"keyword": "报告"},
                "patch": {"title": "偷偷改"},
            }
        )
    with pytest.raises(ValidationError):
        Target(keyword="x", selection_ref="y")
    with pytest.raises(ValidationError):
        Patch()
    with pytest.raises(ValidationError):
        Patch(all_day=1)
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python(
            {
                "schema_version": "4",
                "intent": "none",
                "ambiguities": [],
                "reason": "x",
                "safe_summary": "x",
            }
        )


def test_evidence_requires_real_quote():
    from notido.models import Evidence

    source = [
        {
            "source_id": "s",
            "location": "page:2",
            "state": "read",
            "text": "截止 2026 年 12 月 20 日",
        }
    ]
    assert verify_evidence(
        [Evidence(source_id="s", location="page:2", quote="2026年12月20日")], source
    )
    with pytest.raises(NotiDoError):
        verify_evidence([Evidence(source_id="s", location="page:2", quote="12月21日")], source)


def test_project_does_not_create_or_change_default():
    settings = Settings(default_project="p", allowed_projects=["p", "q"])
    projects = [{"id": "p", "name": "学习"}, {"id": "q", "name": "学习"}]
    assert resolve_project(None, projects, settings)["id"] == "p"
    with pytest.raises(NotiDoError):
        resolve_project("学习", projects, settings)
    with pytest.raises(NotiDoError):
        resolve_project("不存在", projects, settings)
    assert settings.default_project == "p"


def test_notes_preserve_user_region_and_detect_conflict():
    previous = "<!-- NOTIDO:n:START -->\nold\n<!-- NOTIDO:n:END -->"
    replacement = "<!-- NOTIDO:n:START -->\nnew\n<!-- NOTIDO:n:END -->"
    assert (
        replace_managed("用户之前\n" + previous + "\n用户之后", previous, replacement, "n")
        == "用户之前\n" + replacement + "\n用户之后"
    )
    with pytest.raises(NotiDoError):
        replace_managed(previous.replace("old", "edited"), previous, replacement, "n")
    with pytest.raises(NotiDoError):
        replace_managed(previous + previous, previous, replacement, "n")


def test_keys_have_no_delimiter_collision():
    assert key("a:b", "c") != key("a", "b:c")
