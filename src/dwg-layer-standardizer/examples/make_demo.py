"""Generates actual DXF test data, not a pretend screenshot or precomputed report."""
from pathlib import Path
import ezdxf

p = Path(__file__).resolve().parent / "demo_input.dxf"
doc = ezdxf.new("R2013")
for n,c in [("A-WALL",8),("WALL-NEW",7),("A-DOOR",2),("MISC-05",3),("A-TEXT",7),("EMPTY-01",4),("DEFPOINTS",7)]:
    if n not in doc.layers:
        doc.layers.new(n, dxfattribs={"color":c})
m = doc.modelspace()
# Two disjoint rooms / wall line groups.
for x,y in [((0,0),(6000,0)),((6000,0),(6000,4200)),((6000,4200),(0,4200)),((0,4200),(0,0)),((180,180),(5820,180)),((5820,180),(5820,4020)),((5820,4020),(180,4020)),((180,4020),(180,180))]:
    m.add_line(x,y,dxfattribs={"layer":"A-WALL"})
for x,y in [((2200,180),(2200,2500)),((2400,180),(2400,2500)),((2400,2500),(5800,2500))]:
    m.add_line(x,y,dxfattribs={"layer":"WALL-NEW"})
m.add_arc((2400,1000),720,180,270,dxfattribs={"layer":"A-DOOR"})
m.add_line((1680,1000),(2400,1000),dxfattribs={"layer":"A-DOOR"})
m.add_lwpolyline([(3150,2920),(4200,2920),(4200,3600),(3150,3600)],close=True,dxfattribs={"layer":"MISC-05","color":1})
m.add_circle((3660,3260),150,dxfattribs={"layer":"MISC-05"})
m.add_text("LIVING",dxfattribs={"layer":"A-TEXT","height":200,"insert":(3600,2000)})
doc.blocks.new("CHAIR_BLOCK").add_circle((0,0),125,dxfattribs={"layer":"A-DOOR"})
m.add_blockref("CHAIR_BLOCK", (4700,3300),dxfattribs={"layer":"MISC-05"})
doc.saveas(p)
print(p)
