from mod import modhook

# Thanks to https://gist.github.com/anpage/9b5ec3d72200117e224b2e696e8b4280 for the bug description.
# The fix we use here is to overwrite the fuel with expected fuel after recharge.
#
# Player jump-jet charge is i32[mech + 0xC0].
# player_slot = u32[0x0A5918]
# player_entity = u32[0x108B00 + player_slot * 4]
# mech = u32[player_entity + 0x20]
# Charge below 0 means jumpjets are not equipped.
# 0x0A8411 is the jumpjet input trigger.
# 0x0002CD11 is the one-shot loading_screen_setup callsite at mission bring-up.

ADDR_PLAYER_SLOT = 0x000A5918
ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_JUMPJET_TRIGGER = 0x0A8411
ADDR_MISSION_START_CALL = 0x0002CD11
ADDR_FRAME_HOOK = 0x0002CE84

OFFSET_ENTITY_MECH = 0x20
OFFSET_MECH_JUMPJET_CHARGE = 0xC0

JUMPJET_TICK_RATE = 182.0
JUMPJET_TICKS_PER_FUEL = 4.0
JUMPJET_FUEL_MAX = 0x71C


@modhook("MW2.EXE", ADDR_MISSION_START_CALL, "call")
def reset_jumpjet_fuel_recharge(modstate, _gamemem):
    modstate.jumpjet_fix_base_fuel = -1
    modstate.jumpjet_fix_base_time = None


@modhook("MW2.EXE", ADDR_FRAME_HOOK, "call")
def fix_jumpjet_fuel_recharge(modstate, gamemem):
    player_slot = gamemem.read_reloc_u32(ADDR_PLAYER_SLOT)
    player_entity = gamemem.read_reloc_u32(
        ADDR_ENTITY_BODY_TABLE + player_slot * 4
    )
    if player_entity == 0:
        return
    mech = gamemem.read_runtime_u32(player_entity + OFFSET_ENTITY_MECH)
    if mech == 0:
        return
    fuel = gamemem.read_runtime_i32(mech + OFFSET_MECH_JUMPJET_CHARGE)
    if fuel < 0:
        return
    jumpjet_active = gamemem.read_reloc_u8(ADDR_JUMPJET_TRIGGER) != 0

    if jumpjet_active or modstate.jumpjet_fix_base_time is None:
        modstate.jumpjet_fix_base_fuel = fuel
        modstate.jumpjet_fix_base_time = modstate.time
        return

    elapsed = modstate.time - modstate.jumpjet_fix_base_time
    if elapsed <= 0.0:
        return

    recharge = int((elapsed * JUMPJET_TICK_RATE) // JUMPJET_TICKS_PER_FUEL)
    expected_fuel = min(JUMPJET_FUEL_MAX, modstate.jumpjet_fix_base_fuel + recharge)
    if expected_fuel != fuel:
        gamemem.write_reloc_i32(
            (mech + OFFSET_MECH_JUMPJET_CHARGE - gamemem.delta) & 0xFFFFFFFF,
            expected_fuel,
        )
