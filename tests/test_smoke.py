import wraithnet.config as config


def test_config_constants_are_sane():
    assert config.GRID_WIDTH > 0
    assert config.GRID_HEIGHT > 0
    assert config.MAX_GRID_DISTANCE == (config.GRID_WIDTH**2 + config.GRID_HEIGHT**2) ** 0.5
    assert config.UTILITY_W1 > 0
    assert config.UTILITY_W_WEAPON > 0
    assert config.SENSOR_RADIUS > 0
    assert len(config.SCENARIO["targets"]) == 2
    assert len(config.SCENARIO["jets"]) == 3
    assert len(config.SCENARIO["sam_sites"]) == 1
    assert len(config.SCENARIO["jammers"]) == 1
    assert config.JET_HP > 0
    assert config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].max_speed == config.JET_SPEED
    assert config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].hp == config.JET_HP
    assert set(config.JET_FRAME_CATALOG) == {"INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"}
    assert config.SAM_ENGAGEMENT_RANGE > 0
    assert config.SAM_DAMAGE_PER_TICK > 0
    assert config.JAM_RADIUS > 0
    assert config.CONTRACTOR_PENDING_TTL_TICKS > 0


def test_scenario_no_single_jet_can_solo_target_a():
    """Regression guard for the scenario property the whole design depends on
    (see docs/00_Design_Review_Findings.md item F1 / v3 §2): Target_A must
    require more JDAM than any single jet carries, so at least two jets are
    structurally required to destroy it, regardless of utility tuning."""
    jets = config.SCENARIO["jets"]
    targets = {t["target_id"]: t for t in config.SCENARIO["targets"]}
    max_single_jdam = max(j["payload"].get("JDAM", 0) for j in jets)
    assert targets["Target_A"]["target_type"] == "HARDENED_BUNKER"
    assert max_single_jdam < targets["Target_A"]["hp_required"]


def test_scenario_target_b_requires_agm65_and_is_soloable():
    """Literal SCENARIO still lists AIM9X; migration converts to AGM65."""
    from wraithnet.scenario_schema import scenario_from_data

    targets = {t["target_id"]: t for t in config.SCENARIO["targets"]}
    assert targets["Target_B"]["target_type"] == "MOBILE_RADAR"
    assert targets["Target_B"]["hp_required"] == 1
    scenario = scenario_from_data(config.SCENARIO)
    agm_carriers = [j for j in scenario.jets if j.payload.get("AGM65", 0) > 0]
    assert len(agm_carriers) == 1
    assert agm_carriers[0].payload["AGM65"] >= targets["Target_B"]["hp_required"]
