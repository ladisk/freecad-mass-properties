# Mass Properties addon: dock panel with the mass properties of the selected solids.


def _mass_properties_init():
    from massprops import gui

    gui.register()


_mass_properties_init()
