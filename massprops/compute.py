"""Mass properties of solid bodies, independent of the GUI.

All results are in SI units (kg, m). The inertia matrix is stored in tensor
form: moments of inertia on the diagonal and ``-Ixy`` off the diagonal, where
``Ixy = ∫ x y dm`` is the product of inertia.
"""

from dataclasses import dataclass

import numpy as np

import FreeCAD as App
import Part

MM = 1e-3
DEFAULT_MATERIAL_UUID = "7f9fd73b-50c9-41d8-b7b2-575a030c1eeb"
AXES_NAME = "PrincipalAxes"
LCS_TYPES = ("App::LocalCoordinateSystem", "PartDesign::CoordinateSystem")


def has_solid(obj):
    """True if the object has a solid with positive volume (and is not our axes)."""
    if obj is None or obj.Name.startswith(AXES_NAME):
        return False
    try:
        return any(s.Volume > 0 for s in obj.Shape.Solids)
    except Exception:
        return False


def is_lcs(obj):
    return any(obj.isDerivedFrom(t) for t in LCS_TYPES)


def global_placement(obj):
    try:
        return obj.getGlobalPlacement()
    except Exception:
        return obj.Placement


def material_density(obj):
    """Density of the object's FreeCAD material in kg/m³, or None if it has none.

    The built-in "Default" material counts as no material.
    """
    mat = getattr(obj, "ShapeMaterial", None)
    if mat is None or mat.UUID == DEFAULT_MATERIAL_UUID:
        return None
    try:
        if not mat.hasPhysicalProperty("Density"):
            return None
        return float(mat.getPhysicalValue("Density").getValueAs("kg/m^3"))
    except Exception:
        return None


def material_name(obj):
    mat = getattr(obj, "ShapeMaterial", None)
    return mat.Name if mat is not None else ""


@dataclass
class Frame:
    """Reference frame: origin [m] and rotation whose columns are the frame axes."""

    name: str
    origin: np.ndarray
    R: np.ndarray

    @classmethod
    def from_placement(cls, name, pl):
        rot = pl.Rotation
        R = np.column_stack([np.array(rot.multVec(App.Vector(*e))) for e in np.eye(3)])
        return cls(name, np.array(pl.Base) * MM, R)

    @classmethod
    def global_frame(cls, name="Global origin"):
        return cls(name, np.zeros(3), np.eye(3))


@dataclass
class Props:
    """Mass properties of one body or of several bodies together."""

    label: str
    mass: float  # kg
    volume: float  # m³
    area: float  # m²
    com: np.ndarray  # center of mass, global coordinates [m]
    J: np.ndarray  # inertia tensor at com, global axes [kg m²]
    density: float = float("nan")  # kg/m³
    density_source: str = ""
    frame: Frame = None  # body frame (origin and axes of the object)

    def inertia_at(self, frame):
        """Inertia tensor about the frame origin, in the frame axes (parallel axis theorem)."""
        d = self.com - frame.origin
        J_o = self.J + self.mass * (d @ d * np.eye(3) - np.outer(d, d))
        return frame.R.T @ J_o @ frame.R

    def com_in(self, frame):
        return frame.R.T @ (self.com - frame.origin)

    def principal(self):
        """Principal moments (ascending) and axes as columns of a right-handed matrix."""
        w, v = np.linalg.eigh(self.J)
        # make the dominant component of axes 1 and 2 positive, axis 3 completes a right-handed set
        for k in (0, 1):
            if v[np.argmax(np.abs(v[:, k])), k] < 0:
                v[:, k] *= -1
        v[:, 2] = np.cross(v[:, 0], v[:, 1])
        return w, v

    def principal_frame(self):
        return Frame("Center of mass, principal axes", self.com, self.principal()[1])


def _tensor_about(J_c, m, d):
    return J_c + m * (d @ d * np.eye(3) - np.outer(d, d))


def body_props(obj, default_density, use_material=True):
    """Mass properties of one object, with density from its material if available."""
    rho = material_density(obj) if use_material else None
    source = f"material {material_name(obj)}" if rho is not None else "default"
    if rho is None:
        rho = default_density

    pl = global_placement(obj)
    shape = Part.getShape(obj, transform=False).copy()
    shape.Placement = pl
    solids = [s for s in shape.Solids if s.Volume > 0]
    if not solids:
        raise ValueError(f"{obj.Label} has no solid")

    # per solid: volume [mm³], centroid [mm], unit-density tensor at centroid [mm⁵]
    V = np.array([s.Volume for s in solids])
    C = np.array([np.array(s.CenterOfMass) for s in solids])
    Ms = [np.array(s.MatrixOfInertia.A).reshape(4, 4)[:3, :3] for s in solids]
    Vt = V.sum()
    c = (V[:, None] * C).sum(axis=0) / Vt
    J_mm5 = sum(_tensor_about(M, v, ci - c) for M, v, ci in zip(Ms, V, C))

    return Props(
        label=obj.Label,
        mass=rho * Vt * MM**3,
        volume=Vt * MM**3,
        area=shape.Area * MM**2,
        com=c * MM,
        J=rho * J_mm5 * MM**5,
        density=rho,
        density_source=source,
        frame=Frame.from_placement(f"{obj.Label} origin", pl),
    )


def combine(parts, label="Total"):
    """Mass properties of several bodies taken together."""
    if len(parts) == 1:
        return parts[0]
    m = sum(p.mass for p in parts)
    c = sum(p.mass * p.com for p in parts) / m
    J = sum(_tensor_about(p.J, p.mass, p.com - c) for p in parts)
    rho = m / sum(p.volume for p in parts)
    return Props(
        label=label,
        mass=m,
        volume=sum(p.volume for p in parts),
        area=sum(p.area for p in parts),
        com=c,
        J=J,
        density=rho,
        density_source="average",
        frame=parts[0].frame,
    )


def draw_principal_axes(doc, props, length_mm):
    """Lines along the principal axes through the center of mass, global coordinates."""
    c = App.Vector(*(props.com / MM))
    _, v = props.principal()
    lines = []
    for k in range(3):
        a = App.Vector(*v[:, k]) * (length_mm / 2)
        lines.append(Part.makeLine(c - a, c + a))
    f = doc.getObject(AXES_NAME) or doc.addObject("Part::Feature", AXES_NAME)
    f.Shape = Part.makeCompound(lines)
    f.Label = f"Principal axes ({props.label})"
    if App.GuiUp:
        vo = f.ViewObject
        vo.LineWidth = 4
        colors = [(0.88, 0.48, 0.12), (0.16, 0.62, 0.25), (0.12, 0.43, 0.92)]
        try:
            vo.LineColorArray = colors
        except Exception:
            vo.LineColor = colors[0]
    doc.recompute()


def remove_principal_axes(doc):
    if doc.getObject(AXES_NAME):
        doc.removeObject(AXES_NAME)
        doc.recompute()
