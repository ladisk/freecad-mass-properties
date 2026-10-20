"""Checks of massprops.compute against closed-form results.

Run with FreeCAD's command-line interpreter:

    freecadcmd tests/test_compute.py
"""

import os
import sys

import numpy as np

import FreeCAD as App
import Part

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from massprops import compute  # noqa: E402

RHO = 7850.0
a, b, c = 0.3, 0.2, 0.1  # m
m = RHO * a * b * c


def box(doc, name, pos=(0, 0, 0), rot=App.Rotation(), size=(a, b, c)):
    o = doc.addObject("Part::Box", name)
    o.Length, o.Width, o.Height = (s * 1e3 for s in size)
    o.Placement = App.Placement(App.Vector(*pos) * 1e3, rot)
    return o


def close(x, y, tol=1e-9):
    assert np.allclose(x, y, rtol=1e-7, atol=tol), f"\n{x}\n!=\n{y}"


def test_box_at_center_and_corner(doc):
    o = box(doc, "B1")
    doc.recompute()
    p = compute.body_props(o, RHO)
    close(p.mass, m)
    close(p.com, [a / 2, b / 2, c / 2])
    close(p.J, np.diag([m * (b**2 + c**2) / 12, m * (a**2 + c**2) / 12, m * (a**2 + b**2) / 12]))
    J_O = p.inertia_at(p.frame)
    close(J_O[0, 0], m * (b**2 + c**2) / 3)
    close(J_O[0, 1], -m * a * b / 4)  # tensor form: -Ixy


def test_rotated_box_keeps_principal_moments(doc):
    p0 = compute.body_props(doc.getObject("B1"), RHO)
    rot = App.Rotation(App.Vector(1, 2, 3), 37)
    o = box(doc, "B2", pos=(1, -2, 0.5), rot=rot)
    doc.recompute()
    p = compute.body_props(o, RHO)
    close(p.principal()[0], np.sort(np.diag(p0.J)))
    # in the body's own axes through the center of mass, the tensor is diagonal again
    f = compute.Frame("body axes at com", p.com, p.frame.R)
    close(p.inertia_at(f), p0.J)


def test_two_boxes_equal_one_long_box(doc):
    o1 = box(doc, "H1", pos=(0, 0, 2))
    o2 = box(doc, "H2", pos=(a, 0, 2))
    o3 = box(doc, "Long", pos=(0, 0, 2), size=(2 * a, b, c))
    doc.recompute()
    tot = compute.combine([compute.body_props(o1, RHO), compute.body_props(o2, RHO)])
    one = compute.body_props(o3, RHO)
    close(tot.mass, one.mass)
    close(tot.com, one.com)
    close(tot.J, one.J)


def test_compound_of_solids(doc):
    o1 = box(doc, "C1", pos=(0, 0, 5))
    o2 = box(doc, "C2", pos=(a, 0, 5))
    comp = doc.addObject("Part::Compound", "Comp")
    comp.Links = [o1, o2]
    doc.recompute()
    p = compute.body_props(comp, RHO)
    close(p.mass, 2 * m)
    close(p.J[0, 0], 2 * m * (b**2 + c**2) / 12)


def test_partdesign_body_in_moved_container(doc):
    body = doc.addObject("PartDesign::Body", "Body")
    prim = doc.addObject("PartDesign::AdditiveBox", "PBox")
    body.addObject(prim)
    prim.Length, prim.Width, prim.Height = a * 1e3, b * 1e3, c * 1e3
    body.Placement = App.Placement(App.Vector(100, 200, 300), App.Rotation(App.Vector(0, 0, 1), 90))
    doc.recompute()
    for o in (body, prim):
        p = compute.body_props(o, RHO)
        close(p.mass, m)
        close(p.com, [0.1 - b / 2, 0.2 + a / 2, 0.3 + c / 2])


def test_material_density(doc):
    import Materials

    o = box(doc, "Mat", pos=(0, 0, 8))
    steel = next(x for x in Materials.MaterialManager().Materials.values() if x.Name == "Steel-C15")
    o.ShapeMaterial = steel
    doc.recompute()
    p = compute.body_props(o, 1000.0)
    close(p.density, 7800.0)
    assert p.density_source == "material Steel-C15"
    p = compute.body_props(o, 1000.0, use_material=False)
    close(p.density, 1000.0)
    close(compute.body_props(doc.getObject("B1"), 1000.0).density, 1000.0)  # Default material ignored


def test_frames(doc):
    p = compute.body_props(doc.getObject("B1"), RHO)
    g = compute.Frame.global_frame()
    close(p.inertia_at(g), p.inertia_at(p.frame))  # B1 sits at the origin
    w, v = p.principal()
    close(p.inertia_at(p.principal_frame()), np.diag(w))
    pl = App.Placement(App.Vector(1000, 0, 0), App.Rotation(App.Vector(0, 0, 1), 90))
    f = compute.Frame.from_placement("lcs", pl)
    close(p.com_in(f), [b / 2, 1 - a / 2, c / 2])


doc = App.newDocument("massprops_test")
tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
order = ["test_box_at_center_and_corner"]  # creates B1, used by the others
tests.sort(key=lambda t: t.__name__ not in order)
failed = 0
for t in tests:
    try:
        t(doc)
        print(f"ok    {t.__name__}")
    except Exception as e:
        failed += 1
        print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
App.closeDocument(doc.Name)
print(f"{len(tests) - failed}/{len(tests)} passed")
if failed:
    sys.exit(1)
