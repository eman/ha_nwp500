try:
    import nwp500.unit_system
    import inspect
    print("Exports of nwp500.unit_system:")
    for name, obj in inspect.getmembers(nwp500.unit_system):
        if not name.startswith("_"):
            print(name)
except ImportError:
    print("nwp500.unit_system not found")
