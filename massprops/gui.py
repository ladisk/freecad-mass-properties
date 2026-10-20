"""Command, toolbar button and menu entry for the mass properties panel."""

import FreeCADGui as Gui

from massprops import panel

COMMAND = "MassProperties_Panel"


class PanelCommand:
    def GetResources(self):
        return {
            "Pixmap": panel.ICON,
            "MenuText": "Mass Properties",
            "ToolTip": "Show mass, center of mass, inertia matrix and principal axes of the selected solids",
        }

    def Activated(self):
        panel.toggle()

    def IsActive(self):
        return True


class Manipulator:
    """Adds the command to every workbench: View toolbar and Tools menu."""

    def modifyMenuBar(self):
        return [{"insert": COMMAND, "menuItem": "Std_DlgCustomize"}]

    def modifyToolBars(self):
        return [{"append": COMMAND, "toolBar": "View"}]


def register():
    Gui.addCommand(COMMAND, PanelCommand())
    Gui.MassProperties_manipulator = Manipulator()
    Gui.addWorkbenchManipulator(Gui.MassProperties_manipulator)
