import pytest

from custom_components.power_controller.thermodynamics import ThermalZoneModel


def test_1r1c_step_prediction():
    zone = ThermalZoneModel(zone_id="living_room", alpha=1.5, beta=0.05)
    next_temp = zone.predict_next_temp(t_room=20.0, t_out=0.0, power_kw=2.0, dt_hours=1.0)
    assert next_temp == pytest.approx(22.0)


def test_rls_online_calibration_and_window_open_freeze():
    true_alpha, true_beta = 1.8, 0.06
    zone = ThermalZoneModel(zone_id="living_room", alpha=1.0, beta=0.10)
    t_room, t_out = 20.0, 2.0
    for step in range(80):
        p_heat = 2.0 if step % 2 == 0 else 0.0
        t_next = t_room + (true_alpha * p_heat - true_beta * (t_room - t_out)) * 0.25
        zone.update_rls(t_room, t_next, t_out, p_heat, dt_hours=0.25, window_open=False)
        t_room = t_next

    assert zone.alpha == pytest.approx(true_alpha, rel=0.08)
    assert zone.beta == pytest.approx(true_beta, rel=0.08)

    alpha_before, beta_before = zone.alpha, zone.beta
    zone.update_rls(t_room, t_room - 3.0, t_out, 2.0, dt_hours=0.25, window_open=True)
    assert zone.alpha == pytest.approx(alpha_before)
    assert zone.beta == pytest.approx(beta_before)
