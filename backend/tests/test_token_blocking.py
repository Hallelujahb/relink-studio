from app import blocking_diagnostics as bd
from app import matching

CFG = {"strip_parentheticals": True, "strip_suffix_words": True, "keep_digits": True, "case_sensitive": False}


def _run(src_name, tgt_name, fn=matching.method_a_fuzzy, floor=0.3):
    return fn([{"id": "s", "n": src_name}], [{"t": "t1", "n": tgt_name}], "id", "n", "t", "n", CFG, floor)["s"]


def test_blocking_keys_include_whole_prefix_and_token_prefixes():
    assert matching._blocking_keys("the riverside clinic") == {"the", "riv", "cli"}
    assert matching._blocking_keys("") == set()
    assert matching._blocking_keys("a b") == {"a b"[:3]}


def test_leading_article_no_longer_hides_a_match():
    assert _run("The Riverside Clinic", "Riverside Clinic")["targetId"] == "t1"


def test_reordered_name_is_now_a_candidate_for_both_methods():
    assert _run("Clinic Riverside", "Riverside Clinic")["targetId"] == "t1"
    assert _run("Clinic Riverside", "Riverside Clinic", matching.method_b_token_overlap, 0.5)["targetId"] == "t1"


def test_unrelated_names_still_not_compared():
    assert _run("Zzz Nomatch", "Riverside Clinic")["targetId"] is None


def test_candidates_are_deduplicated_across_keys():
    blocks = matching._build_blocks([{"t": "1", "n": "Riverside Riverside Clinic"}], "t", "n", CFG)
    assert len(matching._candidates("riverside clinic", blocks)) == 1


def test_diagnostics_counts_distinct_pairs_and_no_exclusion_for_reordered():
    src = [{"id": "1", "name": "Clinic Riverside"}]
    tgt = [{"tid": "9", "tname": "Riverside Clinic"}]
    r = bd.analyze_blocking(src, tgt, "id", "name", "tid", "tname", CFG)
    assert r["source_rows_excluded_by_blocking"] == 0
    assert r["total_candidate_pairs"] == 1
