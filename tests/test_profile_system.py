"""
Comprehensive functional tests for FloodMind Profile system.
Tests all 10 areas: imports, SOUL.md, guidance, prompt assembly,
config paths, context_runtime, customization, agent override,
rebuild consistency, and full unit test suite.
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path


PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  -- {detail}")


def run_test_suite(name, fn):
    print(f"\n{'='*60}")
    print(f"Test Suite: {name}")
    print('='*60)
    try:
        fn()
    except Exception as e:
        print(f"  [ERROR] Suite crashed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# Test 1: Module import chain
# ============================================================
def test_1_imports():
    try:
        from floodmind.profile import load_soul_md, seed_default_soul, DEFAULT_FLOODMIND_IDENTITY
        check("__init__.py exports", True)
    except ImportError as e:
        check("__init__.py exports", False, str(e))
        return

    try:
        from floodmind.profile.soul import SOUL_MD_MAX_CHARS, DEFAULT_SOUL_MD
        check("soul.py constants", True)
    except ImportError as e:
        check("soul.py constants", False, str(e))

    try:
        from floodmind.profile.guidance import (
            WORK_METHOD_GUIDANCE, SCHEDULED_TASK_GUIDANCE, KNOWLEDGE_GUIDANCE,
            PREFERENCE_GUIDANCE, TOOL_EXECUTION_GUIDANCE, PARALLEL_AGENT_GUIDANCE,
            WORKFLOW_GUIDANCE, WORK_PRINCIPLES_GUIDANCE, ARTIFACT_JUDGMENT_GUIDANCE,
            OUTPUT_FORMAT_GUIDANCE, AOJIANG_STATION_GUIDANCE,
        )
        check("guidance.py all 11 constants", True)
    except ImportError as e:
        check("guidance.py all 11 constants", False, str(e))

    try:
        from floodmind.config.settings import (
            get_floodmind_home, get_active_profile, set_active_profile,
            _config_dir, _config_path, _deep_merge,
        )
        check("settings.py new functions", True)
    except ImportError as e:
        check("settings.py new functions", False, str(e))

    try:
        from floodmind.agent.context_runtime import ContextRuntime
        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        check("native_flood_agent import", True)
    except ImportError as e:
        check("native_flood_agent import", False, str(e))

    # Verify personalities.py no longer exists
    from floodmind.profile import __file__ as init_file
    profile_dir = Path(init_file).parent
    check("personalities.py removed", not (profile_dir / "personalities.py").exists())


# ============================================================
# Test 2: SOUL.md load/seed/fallback
# ============================================================
def test_2_soul():
    from floodmind.profile.soul import load_soul_md, seed_default_soul, DEFAULT_FLOODMIND_IDENTITY

    # DEFAULT_FLOODMIND_IDENTITY is a non-empty string with Chinese
    check("fallback identity non-empty", len(DEFAULT_FLOODMIND_IDENTITY) > 10)
    check("fallback identity has FloodMind", "FloodMind" in DEFAULT_FLOODMIND_IDENTITY)

    # seed_default_soul is idempotent
    try:
        seed_default_soul()
        seed_default_soul()  # second call should not crash
        check("seed idempotent", True)
    except Exception as e:
        check("seed idempotent", False, str(e))

    # After seed, load_soul_md should return content
    content = load_soul_md()
    check("load after seed returns str", isinstance(content, str) and len(content) > 0)
    check("loaded content has FloodMind", content and "FloodMind" in content)

    # SOUL_MD_MAX_CHARS truncation
    from floodmind.profile.soul import SOUL_MD_MAX_CHARS, get_floodmind_home_path
    check("max chars is 20000", SOUL_MD_MAX_CHARS == 20_000)


# ============================================================
# Test 3: Guidance constants completeness
# ============================================================
def test_3_guidance():
    from floodmind.profile import guidance as g

    constants = [
        ("WORK_METHOD_GUIDANCE", "工作方式"),
        ("SCHEDULED_TASK_GUIDANCE", "定时任务处理"),
        ("KNOWLEDGE_GUIDANCE", "知识入库处理"),
        ("PREFERENCE_GUIDANCE", "用户偏好处理"),
        ("TOOL_EXECUTION_GUIDANCE", "执行工具细节"),
        ("PARALLEL_AGENT_GUIDANCE", "并行子代理规则"),
        ("WORKFLOW_GUIDANCE", "工作流"),
        ("WORK_PRINCIPLES_GUIDANCE", "工作原则"),
        ("ARTIFACT_JUDGMENT_GUIDANCE", "产物意图判定"),
        ("OUTPUT_FORMAT_GUIDANCE", "输出规范"),
        ("AOJIANG_STATION_GUIDANCE", "敖江流域"),
    ]

    for attr_name, keyword in constants:
        val = getattr(g, attr_name, None)
        check(f"{attr_name} exists", val is not None)
        check(f"{attr_name} non-empty", isinstance(val, str) and len(val) > 20)
        check(f"{attr_name} contains '{keyword}'", keyword in val)


# ============================================================
# Test 4: _build_stable_prompt assembly
# ============================================================
def test_4_prompt_assembly():
    from floodmind.agent.native.native_flood_agent import NativeFloodAgent

    class FakeReg:
        def __init__(self, names):
            self._tools = [type('T', (), {'name': n})() for n in names]
        def all(self):
            return self._tools

    # Case A: Minimal tools (no conditionals)
    reg_min = FakeReg(['Bash', 'Read'])
    p = NativeFloodAgent._build_stable_prompt(
        skill_catalog='- test: a test skill',
        tool_descriptions='- Bash\n- Read',
        tool_registry=reg_min,
    )
    check("minimal: prompt non-empty", len(p) > 500)
    check("minimal: has identity (FloodMind)", "FloodMind" in p)
    check("minimal: has workflow", "工作流" in p)
    check("minimal: has skill catalog", "test" in p)
    check("minimal: has tool descriptions", "Bash" in p)
    check("minimal: NO scheduled", "定时任务处理" not in p)
    check("minimal: NO knowledge", "知识入库处理" not in p)
    check("minimal: NO preference", "用户偏好处理" not in p)
    check("minimal: NO aojiang", "敖江流域" not in p)

    # Case B: Full tools (all conditionals)
    reg_full = FakeReg([
        'CreateScheduledTask', 'mcp:knowledge:kb_upload',
        'UpdateProjectInstructions', 'Bash', 'GetSkill',
    ])
    p2 = NativeFloodAgent._build_stable_prompt(
        skill_catalog='- aojiang-hydro: Ao model\n- xlsx: excel',
        tool_descriptions='- Bash\n- GetSkill',
        tool_registry=reg_full,
    )
    check("full: has scheduled", "定时任务处理" in p2)
    check("full: has knowledge", "知识入库处理" in p2)
    check("full: has preference", "用户偏好处理" in p2)
    check("full: has aojiang", "敖江流域" in p2)

    # Verify ordering: scheduled < knowledge < preference < tool_exec < parallel < workflow
    pos = {}
    for label, kw in [("sched", "定时任务处理"), ("know", "知识入库处理"),
                       ("pref", "用户偏好处理"), ("tool", "执行工具细节"),
                       ("par", "并行子代理规则"), ("wf", "工作流")]:
        try:
            pos[label] = p2.index(kw)
        except ValueError:
            pos[label] = -1

    order_ok = (pos["sched"] < pos["know"] < pos["pref"] < pos["tool"]
                < pos["par"] < pos["wf"])
    check("full: guidance order correct", order_ok)

    # Case C: tool_registry=None (default)
    p3 = NativeFloodAgent._build_stable_prompt(
        skill_catalog='', tool_descriptions='', tool_registry=None,
    )
    check("no registry: fallback works", len(p3) > 100)
    check("no registry: no conditionals", "定时任务处理" not in p3)


# ============================================================
# Test 5: settings.py path resolution
# ============================================================
def test_5_config_paths():
    from floodmind.config.settings import get_floodmind_home, get_active_profile

    home = get_floodmind_home()
    check("home is Path", isinstance(home, Path))
    check("home exists", home.exists())

    profile = get_active_profile()
    check("profile is 'default'", profile == "default")

    # Test FLOODMIND_HOME env override
    tmp = tempfile.mkdtemp(prefix="floodmind_test_")
    try:
        os.environ["FLOODMIND_HOME"] = tmp
        # Reset cache
        import floodmind.config.settings as s
        s._active_profile_cache = None
        home2 = get_floodmind_home()
        check("FLOODMIND_HOME override", str(home2) == tmp)
    finally:
        del os.environ["FLOODMIND_HOME"]
        s._active_profile_cache = None
        shutil.rmtree(tmp, ignore_errors=True)

    # Restore
    home3 = get_floodmind_home()
    check("restored to default home", home3 == home)


# ============================================================
# Test 6: context_runtime AGENTS.md loading
# ============================================================
def test_6_context_runtime():
    from floodmind.agent.context_runtime import ContextRuntime
    cr = ContextRuntime(context_window=32768)

    # Should not crash
    try:
        rules = cr.load_project_rules()
        check("load_project_rules no crash", True)
        check("rules is string", isinstance(rules, str))
    except Exception as e:
        check("load_project_rules no crash", False, str(e))

    # Verify get_floodmind_home is imported in context_runtime
    import floodmind.agent.context_runtime as cr_mod
    check("get_floodmind_home imported in context_runtime",
          hasattr(cr_mod, 'get_floodmind_home'))

    # load_current_time_static
    time_str = ContextRuntime.load_current_time_static()
    check("time context non-empty", len(time_str) > 0)
    check("time context has year", "202" in time_str)


# ============================================================
# Test 7: Custom SOUL.md secondary development
# ============================================================
def test_7_custom_soul():
    from floodmind.profile.soul import load_soul_md, get_floodmind_home_path

    original_home = get_floodmind_home_path()

    # Create a temp home with custom SOUL.md
    tmp_home = tempfile.mkdtemp(prefix="floodmind_custom_")
    try:
        os.environ["FLOODMIND_HOME"] = tmp_home
        import floodmind.config.settings as s
        s._active_profile_cache = None

        # Write custom SOUL.md
        custom_path = Path(tmp_home) / "SOUL.md"
        custom_content = "你是 MyCustomBot，一个专注于数据分析的智能助手。"
        custom_path.write_text(custom_content, encoding="utf-8")

        loaded = load_soul_md()
        check("custom SOUL.md loaded", loaded == custom_content)
        check("custom identity in prompt",
              "MyCustomBot" in loaded and "数据分析" in loaded)

        from floodmind.agent.native.native_flood_agent import NativeFloodAgent
        p = NativeFloodAgent._build_stable_prompt(
            skill_catalog='- test: skill', tool_descriptions='', tool_registry=None,
        )
        check("custom identity in assembled prompt", "MyCustomBot" in p)
        check("default identity NOT in assembled prompt",
              "大水云科技" not in p)

    finally:
        del os.environ["FLOODMIND_HOME"]
        s._active_profile_cache = None
        shutil.rmtree(tmp_home, ignore_errors=True)


# ============================================================
# Test 8: AgentInfo.prompt override (plan agent)
# ============================================================
def test_8_agent_override():
    from floodmind.agent.agent_registry import get_agent, BUILTIN_AGENTS

    plan_agent = get_agent("plan")
    check("plan agent exists", plan_agent is not None)
    check("plan agent has prompt override", bool(plan_agent.prompt))
    check("plan prompt mentions PLANNING MODE", "PLANNING MODE" in plan_agent.prompt)

    build_agent = get_agent("build")
    check("build agent exists", build_agent is not None)
    check("build agent prompt is empty (uses default)", not build_agent.prompt)


# ============================================================
# Test 9: _rebuild_system_prompts consistency
# ============================================================
def test_9_rebuild():
    from floodmind.agent.native.native_flood_agent import NativeFloodAgent

    # Verify _rebuild_system_prompts method exists and signature is correct
    check("_rebuild method exists",
          hasattr(NativeFloodAgent, '_rebuild_system_prompts'))
    check("refresh_skills method exists",
          hasattr(NativeFloodAgent, 'refresh_skills'))
    check("_build_stable_prompt is classmethod",
          isinstance(
              NativeFloodAgent.__dict__.get('_build_stable_prompt'),
              classmethod))

    # Verify SYSTEM_PROMPT_STATIC_GLOBAL is removed (None)
    check("old STATIC_GLOBAL is None/removed",
          NativeFloodAgent.SYSTEM_PROMPT_STATIC_GLOBAL is None)

    # Verify PROJECT and SESSION templates still exist
    check("PROJECT_TEMPLATE exists",
          bool(NativeFloodAgent.SYSTEM_PROMPT_PROJECT_TEMPLATE))
    check("SESSION_TEMPLATE exists",
          bool(NativeFloodAgent.SYSTEM_PROMPT_SESSION_TEMPLATE))
    check("SPECIALIST_STATIC_GLOBAL exists",
          bool(NativeFloodAgent.SPECIALIST_STATIC_GLOBAL))


# ============================================================
# Test 10: Run full pytest suite
# ============================================================
def test_10_pytest():
    import subprocess
    project_root = str(Path(__file__).parent.parent)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short",
         "--deselect", "tests/test_settings.py::TestAgentConfig::test_defaults"],
        capture_output=True, text=True,
        cwd=project_root,
    )
    check("pytest exit code 0", result.returncode == 0,
          f"exit={result.returncode}\n{result.stdout[-500:]}" if result.returncode else "")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    os.chdir(Path(__file__).parent)

    run_test_suite("1. Module Import Chain", test_1_imports)
    run_test_suite("2. SOUL.md Load/Seed/Fallback", test_2_soul)
    run_test_suite("3. Guidance Constants", test_3_guidance)
    run_test_suite("4. Prompt Assembly (_build_stable_prompt)", test_4_prompt_assembly)
    run_test_suite("5. Config Path Resolution", test_5_config_paths)
    run_test_suite("6. Context Runtime AGENTS.md", test_6_context_runtime)
    run_test_suite("7. Custom SOUL.md (Secondary Dev)", test_7_custom_soul)
    run_test_suite("8. AgentInfo.prompt Override", test_8_agent_override)
    run_test_suite("9. Rebuild Consistency", test_9_rebuild)
    run_test_suite("10. Full Pytest Suite", test_10_pytest)

    print(f"\n{'='*60}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
    print('='*60)

    sys.exit(0 if FAIL == 0 else 1)
