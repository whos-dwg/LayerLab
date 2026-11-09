"""Behavior checks for suffix conventions, content evidence and abstention."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/dwg-layer-standardizer'))
from cadlayer.engine import suggest
from cadlayer.semantic import _geometry_summary, semantic_suggest


def target(name, category, aliases=()):
    return dict(name=name, category=category, aliases=list(aliases), aci=7, linetype='Continuous', lineweight=-3)


STANDARD = {'layers': [
    target('A-wall', 'wall'), target('LA-wall', 'wall'), target('LA-wall-finish', 'wall_finish'),
    target('A-door', 'door'), target('LA-door', 'door'), target('A-hatch', 'hatch'), target('LA-hatch', 'hatch'),
    target('A-text', 'annotation', ['A-TEXT-I']), target('LA-text', 'annotation'), target('RC-text', 'annotation'),
    target('A-dim', 'annotation'), target('A-title', 'annotation'), target('A-grid-dim', 'annotation'),
    target('A-parking', 'parking'), target('A-wind', 'window'), target('LA-curtain', 'curtain'),
    target('A-furn', 'furniture'), target('RC-lighting', 'lighting'),
]}


def layer(name, types=None, **kwargs):
    types = types if types is not None else {'LINE': 10}
    return dict(name=name, total=sum(types.values()), types=types, protected=False, **kwargs)


def classify(name, types=None, standard=STANDARD, **kwargs):
    return suggest({'layers': [layer(name, types, **kwargs)]}, standard)[0]


class LayerRecognitionTests(unittest.TestCase):
    def test_demo_and_custom_annotation_categories_do_not_require_text_suffix(self):
        demo = {'layers': [target('STD-ANNOTATION', 'annotation')]}
        custom = {'layers': [target('NOTES', 'annotation')]}
        for standard in (demo, custom):
            for types in ({'TEXT': 10}, {'DIMENSION': 10}):
                with self.subTest(standard=standard, types=types):
                    self.assertEqual(classify('UNLABELLED', types, standard=standard)['target'],
                                     standard['layers'][0]['name'])

    def test_generic_annotation_fallback_preserves_ambiguity_and_specialized_targets(self):
        ambiguous = {'layers': [target('NOTES', 'annotation'), target('LABELS', 'annotation')]}
        self.assertIsNone(classify('UNLABELLED', {'TEXT': 10}, standard=ambiguous)['target'])
        dimensions = {'layers': [target('CUSTOM-MEASUREMENTS', 'dimension'), target('NOTES', 'annotation')]}
        self.assertEqual(classify('UNLABELLED', {'DIMENSION': 10}, standard=dimensions)['target'],
                         'CUSTOM-MEASUREMENTS')
        self.assertIsNone(classify('UNLABELLED', {'TEXT': 10},
                                  standard={'layers': [target('A-dim', 'annotation')]})['target'])
        self.assertIsNone(classify('UNLABELLED', {'DIMENSION': 10},
                                  standard={'layers': [target('A-text', 'annotation')]})['target'])

    def test_user_examples_and_standard_suffix_variants(self):
        expected = {'A-WALL-BW': 'A-wall', 'A-WALL-DW': 'A-wall', 'RC牆': 'A-wall', 'rc': 'A-wall',
                    'DOOR': 'A-door', 'HATCH': 'A-hatch', 'SITE': 'A-text', '挑空': 'A-text', 'TEXT': 'A-text',
                    'I-TEXT': 'A-text', 'A_text_i': 'A-text', 'Ａ－ＷＡＬＬ－ＢＷ': 'A-wall'}
        for name, wanted in expected.items():
            with self.subTest(name=name):
                result = classify(name)
                self.assertEqual(result['target'], wanted)
                self.assertEqual(result['status'], 'suggested')
                self.assertNotIn('decision', result)

    def test_explicit_discipline_and_longest_suffix_are_preserved(self):
        for name, wanted in [('LA-DOOR-NEW', 'LA-door'), ('RC-TEXT-02', 'RC-text'),
                             ('WALL-FINISH-02', 'LA-wall-finish'), ('GRID-DIM-02', 'A-grid-dim')]:
            with self.subTest(name=name):
                self.assertEqual(classify(name)['target'], wanted)

    def test_whole_segments_not_substrings_or_fuzzy_spelling(self):
        for name in ('OUTDOOR', 'HATCHERY', 'TEXTURE', 'AWALLL', 'Layer_1', 'LAYER_5', 'ref'):
            with self.subTest(name=name):
                self.assertIsNone(classify(name)['target'])

    def test_conflicting_suffixes_and_aliases_abstain(self):
        result = classify('WALL-DOOR')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['target'])
        standard = {'layers': [target('X-one', 'wall', ['shared']), target('X-two', 'door', ['shared'])]}
        self.assertIsNone(classify('shared', standard=standard)['target'])

    def test_missing_convention_target_does_not_choose_other_discipline(self):
        result = classify('SITE', standard={'layers': [target('LA-text', 'annotation')]})
        self.assertIsNone(result['target'])
        self.assertIn('沒有此目標', result['reason'])

    def test_exact_standard_name_outranks_alias_or_convention(self):
        standard = {'layers': [target('RC', 'other'), target('A-wall', 'wall', ['RC'])]}
        self.assertEqual(classify('rc', standard=standard)['target'], 'RC')

    def test_protected_layers_never_map_even_with_false_manifest_flag(self):
        for name in ('0', 'Defpoints', 'xref|DOOR'):
            self.assertEqual(classify(name)['status'], 'protected')
            self.assertIsNone(classify(name)['target'])

    def test_empty_known_names_use_convention_but_unknown_empty_layers_abstain(self):
        self.assertEqual(classify('DOOR', {})['target'], 'A-door')
        self.assertIn('空白圖層', classify('DOOR', {})['reason'])
        self.assertIsNone(classify('OTHER', {})['target'])

    def test_intrinsic_content_distinguishes_text_dimensions_and_hatch(self):
        for types, wanted in [({'TEXT': 90, 'LINE': 10}, 'A-text'),
                              ({'DIMENSION': 85, 'LINE': 15}, 'A-dim'),
                              ({'HATCH': 90, 'LINE': 10}, 'A-hatch')]:
            self.assertEqual(classify('unlabelled', types)['target'], wanted)
        self.assertIsNone(classify('unlabelled', {'TEXT': 50, 'LINE': 50})['target'])

    def test_clear_chinese_names_resolve_with_or_without_contents(self):
        for name, wanted in [('窗', 'A-wind'), ('家具', 'A-furn'), ('內牆', 'A-wall'),
                             ('内墙', 'A-wall'), ('窗戶', 'A-wind'), ('LA-內牆', 'LA-wall'),
                             ('文字', 'A-text'), ('尺寸', 'A-dim'), ('窗簾', 'LA-curtain')]:
            for types in ({}, {'LINE': 10}):
                with self.subTest(name=name, types=types):
                    result = classify(name, types)
                    self.assertEqual(result['target'], wanted)
                    self.assertEqual(result['match_method'], 'semantic_name')
                    self.assertNotIn('decision', result)
        for name in ('衛浴廚房', '窗門', '家具牆'):
            self.assertIsNone(classify(name, {})['target'])

    def test_chinese_name_mapping_stays_local(self):
        self.assertIsNone(classify('窗', {}, standard={'layers': [target('A-wall', 'wall')]})['target'])
        inventory = {'layers': [layer('窗', {}), layer('家具', {}), layer('內牆', {})]}
        with patch('cadlayer.semantic._request_batch') as model:
            result = semantic_suggest(inventory, STANDARD)
        model.assert_not_called()
        self.assertEqual([row['target'] for row in result['suggestions']], ['A-wind', 'A-furn', 'A-wall'])

    def test_zero_fallback_is_only_for_empty_unrecognized_names(self):
        standard = {'layers': [*STANDARD['layers'], target('0', 'system')]}
        for name in ('OTHER', 'ref', '衛浴廚房'):
            result = classify(name, {}, standard=standard)
            self.assertEqual(result['target'], '0')
            self.assertEqual(result['match_method'], 'empty_fallback')
            self.assertNotIn('decision', result)
        self.assertEqual(classify('窗', {}, standard=standard)['target'], 'A-wind')
        self.assertIsNone(classify('unlabelled', {'LINE': 10}, standard=standard)['target'])
        self.assertEqual(classify('unlabelled', {'TEXT': 10}, standard=standard)['target'], 'A-text')
        self.assertIsNone(classify('OTHER', {})['target'])
        self.assertEqual(classify('0', {}, standard=standard)['status'], 'protected')
        self.assertIsNone(classify('Layer_0', {'LINE': 10}, standard=standard)['target'])

    def test_zero_target_does_not_allow_remapping_protected_sources(self):
        from cadlayer import contracts, engine
        standard = {'layers': [*STANDARD['layers'], target('0', 'system')]}
        for module in (contracts, engine):
            self.assertEqual(module.validate_standard(standard)['layers'][-1]['name'], '0')
            for forbidden in ('Defpoints', 'xref|wall'):
                with self.assertRaises(ValueError):
                    module.validate_standard({'layers': [target(forbidden, 'system')]})
            with self.assertRaises(ValueError):
                module.verify_actions({'layers': [layer('0')]}, standard,
                                      [{'source':'0','decision':'map','target':'A-wall'}])

    def test_dxf_zero_destination_preserves_original_zero_properties(self):
        import ezdxf
        from cadlayer.engine import scan_dxf, convert_dxf
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source.dxf'
            doc = ezdxf.new()
            doc.layers.get('0').dxf.color = 2
            doc.layers.get('0').dxf.lineweight = 25
            doc.layers.new('UNKNOWN')
            doc.modelspace().add_line((0,0),(10,0),dxfattribs={'layer':'UNKNOWN'})
            doc.modelspace().add_line((0,1),(10,1),dxfattribs={'layer':'0'})
            doc.saveas(source)
            inventory = scan_dxf(source)
            standard = {'layers': [target('0','system')]}
            actions = [{'source':row['name'],'decision':'map' if row['name']=='UNKNOWN' else 'keep',
                        'target':'0' if row['name']=='UNKNOWN' else None} for row in inventory['layers']]
            result = convert_dxf(source,inventory,standard,actions,root/'output')
            reopened = ezdxf.readfile(root/'output'/result['output'])
            self.assertTrue(all(entity.dxf.layer == '0' for entity in reopened.modelspace()))
            self.assertEqual(reopened.layers.get('0').dxf.color,2)
            self.assertEqual(reopened.layers.get('0').dxf.lineweight,25)
            self.assertTrue(result['report']['source_unchanged'])

    def test_model_cannot_use_zero_for_nonempty_unrecognized_layers(self):
        standard = {'layers': [*STANDARD['layers'],target('0','system')]}
        answer = [{'source':'unlabelled','target':'0','status':'suggested','confidence':.99,'candidates':[]}]
        with patch('cadlayer.semantic._request_batch',return_value=(answer,'test-response')):
            result = semantic_suggest({'layers':[layer('unlabelled')]},standard)
        self.assertIsNone(result['suggestions'][0]['target'])

    def test_layer_usage_is_not_replaced_by_incidental_text_objects(self):
        self.assertEqual(classify('新-車道', {'TEXT': 15})['target'], 'A-parking')

    def test_named_blocks_must_cover_main_contents(self):
        result = classify('unlabelled', {'INSERT': 10}, block_names={'推開門': 9, 'A$C123': 1})
        self.assertEqual(result['target'], 'A-door')
        self.assertEqual(result['match_method'], 'content_evidence')
        self.assertEqual(classify('unlabelled', {'INSERT': 10}, block_names={'DOORD1': 10})['target'], 'A-door')
        for types, blocks in [({'INSERT': 100}, {'推開門': 1, 'A$C123': 99}),
                              ({'INSERT': 2, 'LINE': 98}, {'推開門': 2}),
                              ({'INSERT': 10}, {'A$C123': 10})]:
            self.assertIsNone(classify('unlabelled', types, block_names=blocks)['target'])

    def test_conflicting_or_furniture_door_blocks_do_not_become_building_doors(self):
        mixed = classify('unlabelled', {'INSERT': 10}, block_names={'推開門': 5, '固定窗': 5})
        self.assertEqual(mixed['status'], 'ambiguous')
        self.assertIsNone(mixed['target'])
        minority = classify('unlabelled', {'INSERT': 10}, block_names={'推開門': 9, '固定窗': 1})
        self.assertIsNone(minority['target'], 'A known conflicting block must not disappear behind the dominant category')
        self.assertIsNone(classify('unlabelled', {'INSERT': 10}, block_names={'拉門衣櫃': 10})['target'])
        self.assertEqual(classify('unlabelled', {'INSERT': 10}, block_names={'窗簾': 10})['target'], 'LA-curtain')

    def test_generic_geometric_outlines_never_prove_a_wall(self):
        preview = [{'kind': 'polyline', 'closed': True, 'points': [[0, 0], [100, 0], [100, 5], [0, 5]]}] * 10
        result = classify('unlabelled', {'LWPOLYLINE': 10}, preview=preview)
        self.assertIsNone(result['target'])
        self.assertIn('狹長封閉', result['reason'])

    def test_model_only_receives_unresolved_layers_and_cannot_replace_conventions(self):
        inventory = {'layers': [layer('rc'), layer('SITE'), layer('DOOR'), layer('unlabelled'), layer('xref|WALL')]}
        answer = [{'source': 'unlabelled', 'target': None, 'confidence': .2, 'status': 'unknown', 'candidates': []}]
        with patch('cadlayer.semantic._request_batch', return_value=(answer, 'test-response')) as model:
            result = semantic_suggest(inventory, STANDARD)
        self.assertEqual([row['name'] for row in model.call_args.args[0]], ['unlabelled'])
        by_source = {row['source']: row for row in result['suggestions']}
        self.assertEqual(by_source['rc']['target'], 'A-wall')
        self.assertEqual(by_source['SITE']['engine'], 'rules')
        self.assertIsNone(by_source['unlabelled']['target'])
        self.assertEqual(by_source['xref|WALL']['status'], 'protected')

    def test_uncertain_low_score_or_near_tied_model_answers_have_no_target(self):
        answers = [dict(status='unknown', confidence=.9), dict(status='ambiguous', confidence=.9),
                   dict(status='suggested', confidence=.64), dict(status='suggested', confidence=float('nan')),
                   dict(status='suggested', confidence=.9, candidates=[{'target': 'A-wall', 'confidence': .88}])]
        for answer in answers:
            row = dict(source='unlabelled', target='A-door', candidates=[], **{k: v for k, v in answer.items() if k != 'candidates'})
            row['candidates'] = answer.get('candidates', [])
            with patch('cadlayer.semantic._request_batch', return_value=([row], 'test-response')):
                result = semantic_suggest({'layers': [layer('unlabelled')]}, STANDARD)
            self.assertIsNone(result['suggestions'][0]['target'])

    def test_all_direct_matches_do_not_make_a_model_request(self):
        with patch('cadlayer.semantic._request_batch') as model:
            result = semantic_suggest({'layers': [layer('DOOR'), layer('SITE')]}, STANDARD)
        model.assert_not_called()
        self.assertEqual(result['engine'], 'rules')

    def test_model_geometry_preserves_shape_proportions_curves_and_sampling_limits(self):
        summary = _geometry_summary([
            {'kind': 'polyline', 'points': [[100, 100], [200, 110], ['bad', 0]], 'closed': True, 'has_bulges': True, 'truncated': True},
            {'kind': 'arc', 'center': [150, 105], 'radius': 5, 'start': 0, 'end': 90},
        ])
        poly, arc = summary['normalized_samples']
        self.assertEqual(poly['points'], [[0, 0], [1, .1]])
        self.assertTrue(poly['has_bulges'])
        self.assertTrue(poly['truncated'])
        self.assertEqual(arc['center'], [.5, .05])
        self.assertEqual(arc['end'], 90)
        self.assertEqual(arc['relative_radius'], .05)
        self.assertTrue(summary['uniform_scale'])


if __name__ == '__main__':
    unittest.main()
