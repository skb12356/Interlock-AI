from interlock.gateway.config import Settings


def test_shadow_replay_requires_explicit_data_egress_opt_in() -> None:
    assert Settings().shadow_sample_rate == 0.0
