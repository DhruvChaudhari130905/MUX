import pytest

from mux.rooms.file_access import AccessRules, normalize_folder

OWNER = "olivia"


def rules() -> AccessRules:
    return AccessRules(
        roles={"Backend engineer": ["server"], "Frontend developer": ["web"]},
        member_role={"ravi": "Backend engineer", "fatima": "Frontend developer"},
        member_folders={"ravi": ["docs/api"], "sam": ["infra"]},
    )


def test_folder_prefix_is_segment_aware():
    r = rules()
    assert r.can_change("fatima", "web/src/App.tsx", OWNER)
    assert not r.can_change("fatima", "webhooks/x.ts", OWNER)
    assert normalize_folder(" ./web/ ") == "web"


def test_role_and_personal_folders_add_up():
    r = rules()
    assert r.can_change("ravi", "server/mux/main.py", OWNER)
    assert r.can_change("ravi", "docs/api/auth.md", OWNER)
    assert not r.can_change("ravi", "web/src/App.tsx", OWNER)
    assert r.can_change("sam", "infra/Dockerfile", OWNER)  # personal folders only, no role


def test_owner_and_unconfigured_editors_are_unrestricted():
    r = rules()
    assert r.can_change(OWNER, "web/x.ts", OWNER) and not r.is_restricted(OWNER, OWNER)
    assert r.can_change("newbie", "web/x.ts", OWNER) and not r.is_restricted("newbie", OWNER)


def test_unassigned_files_need_the_owner():
    r = rules()
    assert not r.can_change("ravi", "README.md", OWNER)
    assert r.approvers("README.md", OWNER, ["ravi", "fatima", "sam"]) == {OWNER}


def test_approvers_are_owner_plus_coders_of_that_area():
    r = rules()
    assert r.approvers("web/src/App.tsx", OWNER, ["ravi", "fatima", "sam"]) == {OWNER, "fatima"}
    assert r.can_approve("fatima", "web/src/App.tsx", OWNER)
    assert r.can_approve(OWNER, "web/src/App.tsx", OWNER)
    assert not r.can_approve("ravi", "web/src/App.tsx", OWNER)
    assert not r.can_approve("newbie", "web/src/App.tsx", OWNER)  # unrestricted, but owns no area


@pytest.mark.parametrize("bad", ["", "/", "../etc", "a//b", "a/./b"])
def test_bad_folders_are_refused(bad):
    with pytest.raises(ValueError):
        normalize_folder(bad)
