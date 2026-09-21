import json

from geoagent.report.question_library import (
    load_question_library,
    match_parsed_questions,
    match_question,
)


def _library(tmp_path):
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps({
        'name': 'test',
        'entries': [
            {
                'id': 'total_summary',
                'question': '全库共有多少个变化图斑、总面积是多少',
                'aliases': ['一共有多少图斑'],
                'keywords': ['总数', '总面积', '全库'],
                'intent': '全库汇总',
                'strategy': 'sql',
                'sql': 'SELECT 1',
                'unit': '平方米',
            },
            {
                'id': 'by_county',
                'question': '分区县统计变化图斑数量和总面积',
                'aliases': ['分行政区统计'],
                'keywords': ['区县', '行政区', '面积'],
                'intent': '分县汇总',
                'strategy': 'sql',
                'sql': 'SELECT 1',
                'unit': '平方米',
            },
        ],
    }, ensure_ascii=False), encoding='utf-8')
    return load_question_library(path)


def test_match_returns_explainable_candidates(tmp_path) -> None:
    result = match_question('请查询全库图斑总数和总面积', _library(tmp_path))
    assert result['matched_question_id'] == 'total_summary'
    assert result['confidence'] > 0
    assert result['match_reasons']


def test_match_keeps_human_confirmation_state(tmp_path) -> None:
    questions = [{'id': 'Q1', 'question': '请分行政区统计变化图斑面积', 'status': '待用户确认'}]
    result = match_parsed_questions(questions, _library(tmp_path))
    assert result[0]['question_library_match']['matched_question_id'] == 'by_county'
    assert result[0]['status'] == '待用户确认'


def test_illegal_land_question_does_not_fall_back_to_generic_county(tmp_path) -> None:
    result = match_question('按县统计疑似新增违法建设用地面积', _library(tmp_path))
    assert result['status'] == 'unmatched'
    assert result['candidates'] == []
