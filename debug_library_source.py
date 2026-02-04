import nwp500
import nwp500.unit_system
import inspect

print("Source of nwp500.unit_system.half_celsius_to_preferred:")
try:
    print(inspect.getsource(nwp500.unit_system.half_celsius_to_preferred))
except Exception as e:
    print(f"Error: {e}")

print("\nSource of nwp500.unit_system.div_10_celsius_to_preferred:")
try:
    print(inspect.getsource(nwp500.unit_system.div_10_celsius_to_preferred))
except Exception as e:
    print(f"Error: {e}")

print("\nSource of nwp500.unit_system.convert_temperature:")
try:
    print(inspect.getsource(nwp500.unit_system.convert_temperature))
except Exception as e:
    print(f"Error: {e}")
