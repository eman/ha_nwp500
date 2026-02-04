import nwp500
import inspect

print("Exports of nwp500:")
for name, obj in inspect.getmembers(nwp500):
    if not name.startswith("_"):
        print(name)

try:
    from nwp500 import utils
    print("\nExports of nwp500.utils:")
    for name, obj in inspect.getmembers(utils):
        if not name.startswith("_"):
            print(name)
except ImportError:
    pass

try:
    from nwp500 import conversion
    print("\nExports of nwp500.conversion:")
    for name, obj in inspect.getmembers(conversion):
        if not name.startswith("_"):
            print(name)
except ImportError:
    pass
