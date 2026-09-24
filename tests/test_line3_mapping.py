"""LINE3 階段二：完整映射、解析警告與跨檔主鍵碰撞（純資料測試）。"""
import inspect
from copy import deepcopy

import pytest
from openpyxl.worksheet.cell_range import CellRange

from alarm_ingest.detect import (
    _cell_to_str, _find_precise_header_row, grid_to_alarm_dicts,
    grid_to_rows, read_grid,
)
from alarm_ingest.quality import clean
from test_line3_detect import HEADERS, _line3_fixture_path


def convert(grid, *, device_model=' test model ', existing_keys=None, source='test.xlsx#alarms'):
    return grid_to_alarm_dicts(
        grid, 0, [CellRange('J1:K1')], source, device_model,
        existing_keys=set() if existing_keys is None else existing_keys)


def make_grid(number=3250.0, alarm_type='A'):
    return [HEADERS + [None], [None] * 9 + ['PROD', 'MM'],
            [alarm_type, number, ' HMI\n message ', ' alarm\r\n cause ',
             ' do\t this\n now ', 'simulation', 'effects', 'reset', 'validation', 'x', '-']]


def keys(rows):
    return {(r['device_model'], r['code'], r['variant']) for r in rows}


def test_five_real_files_all_214_rows():
    seen = set()
    total = 0
    for filename, count, names in [
        ('22062QE6SP63_LF.xlsx', 4, ['PROD', 'MM']),
        ('22062QE5SP63_02.xlsx', 92, ['PM', 'ES', 'LT', 'VHP', 'EA', 'CL', 'MM']),
        ('22062QE6SP63_DE.xlsx', 20, ['PREP', 'PROD', 'PE', 'MM', 'CS', 'VHP']),
        ('22062QE6SP63_RI.xlsx', 95, ['PREP', 'PROD', 'PE', 'MM', 'CS', 'VHP']),
        ('22062QE6SP63_DB_01.xlsx', 3, ['PREP', 'PROD', 'PE', 'MM', 'CS', 'VHP']),
    ]:
        file_count = 0
        for sheet, grid, merges in read_grid(_line3_fixture_path(filename), with_merges=True):
            header = _find_precise_header_row(grid)
            if header is None:
                continue
            source = filename + '#' + sheet
            rows = grid_to_alarm_dicts(grid, header, merges, source, 'fixture model',
                                       existing_keys=seen)
            originals = [r for r in grid[header + 2:]
                         if len(r) > 1 and _cell_to_str(r[1]).strip()]
            assert len(rows) == len(originals)
            for actual, original in zip(rows, originals):
                assert actual == {
                    'code': _cell_to_str(original[1]).strip(), 'variant': '',
                    'device_model': 'fixture model',
                    'severity': {'A': '警告', 'W': '資訊'}[original[0].strip().upper()],
                    'description': clean(original[2]), 'cause': clean(original[3]),
                    'solution': clean(original[4]),
                    'operating_modes': [name for col, name in enumerate(names, 9)
                                        if str(original[col]).strip().lower() == 'x'],
                    '_source': source, '_warnings': [],
                }
            seen.update(keys(rows))
            file_count += len(rows)
        assert file_count == count
        total += file_count
    assert total == len(seen) == 214


@pytest.mark.parametrize('number,expected', [(3250, '3250'), (3250.0, '3250'),
                                             ('003250', '003250'), (0, '0'), ('1', '1')])
@pytest.mark.parametrize('alarm_type,severity', [(' A ', '警告'), ('w', '資訊'), ('Ｗ', '資訊')])
def test_mapping_normalization_and_omissions(number, expected, alarm_type, severity):
    grid = make_grid(number, alarm_type)
    before = deepcopy(grid)
    row, = convert(grid)
    assert row == {
        'code': expected, 'variant': '', 'device_model': ' test model ',
        'severity': severity, 'description': 'HMI message', 'cause': 'alarm cause',
        'solution': 'do this now', 'operating_modes': ['PROD'],
        '_source': 'test.xlsx#alarms', '_warnings': [],
    }
    assert grid == before


def test_cross_file_collision_and_input_unchanged():
    first = convert(make_grid(), source='first.xlsx')
    seen = keys(first)
    before = seen.copy()
    with pytest.raises(ValueError) as error:
        convert(make_grid('3250', 'W'), existing_keys=seen, source='second.xlsx')
    assert repr((' test model ', '3250', '')) in str(error.value)
    assert 'second.xlsx' in str(error.value)
    assert seen == before
    assert convert(make_grid(), existing_keys=seen, device_model='another model')
    assert seen == before


def test_within_file_collision():
    grid = make_grid()
    grid.append(grid[-1].copy())
    with pytest.raises(ValueError, match='重複 alarm 主鍵'):
        convert(grid)


@pytest.mark.parametrize('value', ['unexpected', '', None, 1])
def test_unknown_type_and_modes_warnings_preserved(value):
    grid = make_grid(alarm_type=value)
    grid[-1][10] = 'yes'
    row, = convert(grid)
    assert row['severity'] is None
    assert row['operating_modes'] == ['PROD']
    assert row['_warnings'] == [
        {'field': 'MM', 'row': 2, 'column': 10, 'raw_value': 'yes'},
        {'field': 'Alarm Type', 'row': 2, 'column': 0, 'raw_value': _cell_to_str(value)},
    ]


@pytest.mark.parametrize('number', [None, '', '  '])
def test_empty_number_skipped(number):
    assert convert(make_grid(number)) == []


@pytest.mark.parametrize('number', ['123text', '1.5', 3250.5, '-12', '123\n456'])
def test_invalid_number_rejected(number):
    with pytest.raises(ValueError, match='無效 Alarm Number'):
        convert(make_grid(number))


def test_modes_absent_versus_disabled():
    grid = make_grid()
    grid[-1][9] = '-'
    assert convert(grid)[0]['operating_modes'] == []
    grid[0][9] = 'unrelated'
    assert convert(grid)[0]['operating_modes'] is None


def test_required_arguments_and_header_validation():
    with pytest.raises(TypeError):
        grid_to_alarm_dicts(make_grid(), 0, [], 'source', existing_keys=set())
    with pytest.raises(TypeError):
        grid_to_alarm_dicts(make_grid(), 0, [], 'source', 'model')
    with pytest.raises(TypeError, match='device_model'):
        convert(make_grid(), device_model=None)
    grid = make_grid()
    grid[0] = grid[0].copy()
    grid[0][2] = 'missing'
    with pytest.raises(ValueError, match='hmi message'):
        convert(grid)


def test_legacy_signature_and_output():
    assert str(inspect.signature(grid_to_rows)) == "(grid: 'list', mapping: 'tuple', source: 'str') -> 'list'"
    assert grid_to_rows([['3250 message', ' cause\ntext ', ' do\tthis '], ['', '', '']],
                        (0, 1, 2, 0), 'legacy') == [
        {'code': '3250', 'variant': 'message', 'cause': 'cause text',
         'action': 'do this', '_source': 'legacy'}]
