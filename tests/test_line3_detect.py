"""LINE3 批次一：讀檔介面與 A/B/C 表頭判定，不展開 Operating Modes。"""
import os
from pathlib import Path

import openpyxl
import pytest

from alarm_ingest.detect import (
    _find_precise_header_row, _normalize_header_cell, detect_columns,
    list_sheets, read_grid, read_tabular,
)

HEADERS = [
    'Alarm Type', 'Alarm Number', 'HMI Message', 'Alarm Description',
    'Solution', 'Alarm Simulation', 'Alarm Effects', 'Alarm Reset',
    'Validation Test', 'Operating Modes',
]
ROOT = Path(__file__).resolve().parents[1]
FILL = ROOT / 'tools/variant/fixtures/FILL203_batch_report_alarm_list_260529_ENG_revise.xlsx'


def data(number):
    return ['A', number, 'message', 'description', 'solution']


def test_normalization():
    assert _normalize_header_cell('  ＡＬＡＲＭ\n Ｎｕｍｂｅｒ　') == 'alarm number'
    assert _normalize_header_cell(None) == ''


def test_grid_merges_and_sheet_selection(tmp_path):
    path = tmp_path / 'merged.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'alarms'
    ws.append(HEADERS)
    ws.merge_cells('J1:L1')
    ws.merge_cells('A1:A2')
    wb.create_sheet('empty')
    wb.save(path)
    wb.close()
    assert all(len(item) == 3 and item[2] == [] for item in read_grid(path))
    direct = openpyxl.load_workbook(path, data_only=True)
    try:
        actual = read_grid(path, with_merges=True)
        assert [(n, set(map(str, m))) for n, _, m in actual] == [
            (ws.title, set(map(str, ws.merged_cells.ranges))) for ws in direct
        ]
    finally:
        direct.close()
    assert len(read_grid(path, sheet='alarms', with_merges=True)) == 1
    with pytest.raises(ValueError, match='找不到分頁'):
        read_grid(path, sheet='missing')


def test_csv_and_existing_callers(tmp_path):
    path = tmp_path / 'old.csv'
    path.write_text('Description,Cause,Action\n' + ''.join(
        f'{100+i} message,cause,action\n' for i in range(5)), encoding='utf-8-sig')
    assert read_grid(path, with_merges=True)[0][2] == []
    assert read_grid(path)[0][2] == []
    assert list_sheets(path) == [('csv', True)]
    assert len(read_tabular(path)) == 5


@pytest.mark.parametrize('row_number', [58, 65, 75])
def test_deep_header_synthetic(row_number):
    grid = [['title']] * (row_number - 1) + [HEADERS, [], data(2945)]
    assert _find_precise_header_row(grid) == row_number - 1
    assert detect_columns(grid) == (1, 3, 4, row_number + 1)


@pytest.mark.parametrize('numbers,expected', [
    ([1, 2, 3, 4, 'bad'], 0),
    ([1, 2, 3, 'bad', 'bad'], None),
    ([0.0], 0), ([1.5], None), ([], None), (['123x'], None),
])
def test_condition_b_threshold(numbers, expected):
    grid = [HEADERS, []] + [data(n) for n in numbers] + [[], [None, '  ']]
    assert _find_precise_header_row(grid) == expected


def test_condition_a_exact_and_minimum():
    assert _find_precise_header_row([HEADERS[:6], [], data(1)]) == 0
    assert _find_precise_header_row([HEADERS[:5], [], data(1)]) is None
    assert _find_precise_header_row([[h + ' explanation' for h in HEADERS], [], data(1)]) is None
    missing_number = [h for h in HEADERS if h != 'Alarm Number']
    assert _find_precise_header_row([missing_number, [], data(1)]) is None


def test_legend_rejected_by_b_then_later_header_found():
    grid = [HEADERS, []] + [['legend', 'not a number']] * 8
    grid += [HEADERS, [], data(123)]
    assert _find_precise_header_row(grid) == 10


def test_condition_c_later_header_wins_tie():
    # Both candidates pass B: the earlier one has 5/6 numeric rows.
    grid = [HEADERS, [], HEADERS, []] + [data(n) for n in range(5)]
    assert _find_precise_header_row(grid) == 2


def test_condition_c_more_hits_win_before_later_row():
    grid = [HEADERS, [], HEADERS[:6], []] + [data(n) for n in range(5)]
    assert _find_precise_header_row(grid) == 0


def test_fill203_real_fixture_unchanged():
    expected = {'alarm count': (1, 2, 3, 1), 'alarm list': (0, 1, 2, 2),
                'Problem': None, '工作表1': None}
    grids = read_grid(FILL)
    assert {name: detect_columns(grid) for name, grid, _ in grids} == expected
    assert all(_find_precise_header_row(grid) is None for _, grid, _ in grids)
    assert list_sheets(FILL) == [(name, result is not None) for name, result in expected.items()]
    assert read_tabular(FILL, sheet='alarm list')


@pytest.mark.parametrize('filename,row_number', [
    ('22062QE6SP63_LF.xlsx', 58), ('22062QE5SP63_02.xlsx', 65),
    ('22062QE6SP63_DE.xlsx', 75), ('22062QE6SP63_RI.xlsx', 75),
    ('22062QE6SP63_DB_01.xlsx', 75),
])
def test_line3_real_fixture(filename, row_number):
    fixture_dir = os.environ.get('LINE3_FIXTURE_DIR')
    if fixture_dir:
        path = Path(fixture_dir) / filename
        assert path.is_file(), f'Missing required fixture: {path}'
    else:
        paths = list(ROOT.rglob(filename))
        if not paths:
            pytest.skip('LINE3 原始 Excel 未提供；請設定 LINE3_FIXTURE_DIR')
        path = paths[0]
    grids = read_grid(path, with_merges=True)
    assert any(_find_precise_header_row(grid) == row_number - 1 for _, grid, _ in grids)
    direct = openpyxl.load_workbook(path, data_only=True)
    try:
        for name, grid, merges in grids:
            assert set(map(str, merges)) == set(map(str, direct[name].merged_cells.ranges))
            if _find_precise_header_row(grid) == row_number - 1:
                assert detect_columns(grid)[3] == row_number + 1
    finally:
        direct.close()
