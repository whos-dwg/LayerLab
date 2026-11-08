"""Behavior tests for conservative object routing and authorization boundaries."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/dwg-layer-standardizer'))
from cadlayer.objects import classify, validate_object_actions

ALLOWED = {'A-wall','A-col','LA-wall','LA-door','LA-text','LA-dim','LA-hatch','LA-furn','LA-sanitary','RC-lighting'}

def obj(**kw):
    return dict(handle='ABC', source='1_新增隔間牆', type='LINE', context='model', **kw)

class ObjectRoutingTests(unittest.TestCase):
    def test_same_layer_routes_by_object_type(self):
        base = obj()
        text = {**base,'type':'TEXT','text':'隔間備註'}
        dim = {**base,'handle':'ABD','type':'DIMENSION'}
        self.assertEqual(classify(text,ALLOWED)[0],'LA-text')
        self.assertEqual(classify(dim,ALLOWED)[0],'LA-dim')
        self.assertIsNone(classify(base,ALLOWED)[0])

    def test_demolition_system_and_shared_contents_never_move(self):
        for source,context in [('0','model'),('Defpoints','model'),('x|wall','model'),('0_原有拆除','model'),('牆','block')]:
            self.assertIsNone(classify({**obj(),'source':source,'context':context,'type':'DIMENSION'},ALLOWED)[0])

    def test_closet_and_conflicting_block_are_not_building_doors(self):
        for name,source in [('拉門衣櫃','2_高櫃'),('推開門','6_插座點位')]:
            self.assertIsNone(classify({**obj(),'type':'INSERT','block_name':name,'source':source},ALLOWED)[0])

    def test_unknown_and_unsupported_targets_stay(self):
        self.assertIsNone(classify({**obj(),'type':'INSERT','block_name':'A$C123'},ALLOWED)[0])
        self.assertIsNone(classify({**obj(),'type':'INSERT','block_name':'推開門'},set())[0])

    def test_wall_geometry_requires_closed_straight_slender_polygon(self):
        g=dict(kind='polyline',closed=True,points=[[0,0],[100,0],[100,5],[0,5]],has_bulges=False)
        wall={**obj(),'type':'LWPOLYLINE','geometry':g}
        self.assertEqual(classify(wall,ALLOWED)[0],'LA-wall')
        for change in [dict(closed=False),dict(has_bulges=True),dict(truncated=True),dict(points=[[0,0],[5,0],[5,5],[0,5]])]:
            self.assertIsNone(classify({**wall,'geometry':{**g,**change}},ALLOWED)[0])

    def test_stale_duplicate_shared_and_nonstandard_decisions_rejected(self):
        original={**obj(),'type':'TEXT','text':'註記'}
        inventory={'objects':[original]}
        standard={'layers':[{'name':'LA-text'}]}
        decision={**original,'target':'LA-text','decision':'map'}
        validate_object_actions(inventory,standard,[decision])
        for rows in [[decision,decision],[{**decision,'handle':'BAD'}],[{**decision,'target':'invented'}],[{**decision,'type':'INSERT'}]]:
            with self.assertRaises(ValueError):validate_object_actions(inventory,standard,rows)
        shared={**original,'context':'block'}
        with self.assertRaises(ValueError):validate_object_actions({'objects':[shared]},standard,[{**shared,'target':'LA-text','decision':'map'}])

if __name__=='__main__':unittest.main()
