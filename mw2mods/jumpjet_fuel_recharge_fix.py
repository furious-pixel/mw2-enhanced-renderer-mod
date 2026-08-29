from mod import modhook

# Thanks to https://gist.github.com/anpage/9b5ec3d72200117e224b2e696e8b4280 for the bug description.
# The fix we use here is to overwrite the fuel with expected fuel after recharge.

ADDR_JUMPJET_FUEL = 0x0028E29C
ADDR_JUMPJET_ACTIVE = 0x0A8411
ADDR_FRAME_HOOK = 0x0002CE84

JUMPJET_TICK_RATE = 182.0
JUMPJET_TICKS_PER_FUEL = 4.0
JUMPJET_FUEL_MAX = 1820

@modhook("MW2.EXE", ADDR_FRAME_HOOK, "call")
def fix_jumpjet_fuel_recharge(modstate, gamemem):
    fuel = gamemem.read_reloc_u16(ADDR_JUMPJET_FUEL)
    active = gamemem.read_reloc_u8(ADDR_JUMPJET_ACTIVE) != 0

    if not hasattr(modstate, "jumpjet_fix_prev_active"):
        modstate.jumpjet_fix_prev_active = active
        modstate.jumpjet_fix_base_fuel = fuel
        modstate.jumpjet_fix_base_time = modstate.time

    if active:
        modstate.jumpjet_fix_prev_active = True
        modstate.jumpjet_fix_base_fuel = fuel
        modstate.jumpjet_fix_base_time = modstate.time
        return

    if modstate.jumpjet_fix_prev_active:
        modstate.jumpjet_fix_prev_active = False
        modstate.jumpjet_fix_base_fuel = fuel
        modstate.jumpjet_fix_base_time = modstate.time
        return

    elapsed = modstate.time - modstate.jumpjet_fix_base_time
    if elapsed <= 0.0:
        return

    recharge = int((elapsed * JUMPJET_TICK_RATE) // JUMPJET_TICKS_PER_FUEL)
    expected_fuel = min(JUMPJET_FUEL_MAX, modstate.jumpjet_fix_base_fuel + recharge)
    if expected_fuel != fuel:
        gamemem.write_reloc_u16(ADDR_JUMPJET_FUEL, expected_fuel)

    if expected_fuel >= JUMPJET_FUEL_MAX:
        modstate.jumpjet_fix_base_fuel = JUMPJET_FUEL_MAX
        modstate.jumpjet_fix_base_time = modstate.time
