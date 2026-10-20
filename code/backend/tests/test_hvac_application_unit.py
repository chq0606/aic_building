"""Model estimates must not be presented as metered savings or ignore discomfort."""
import pytest
from app.services.hvac_application_service import cooling_plan


VALUES = dict(room_c=25.5, outdoor_c=32., lower_c=23., upper_c=26., internal_kw=.3, solar_kw_m2=.3)


@pytest.fixture(scope='module')
def plan_result():
    return cooling_plan(VALUES)


def test_electricity_is_full_period_cooling_and_fan_integral(plan_result):
    assert plan_result['scope'] == 'frozen_model_estimate_only'
    for policy in plan_result['policies'].values():
        rows = policy['series']
        assert [r['minute'] for r in rows] == list(range(61))
        # Independently integrate the physical actuator power, using previous T.
        expected = 0.
        for previous, current in zip(rows, rows[1:]):
            fan = current['fan_fraction']
            assert 0 <= fan <= 1
            cooling_kw = .55*1.006*max(previous['room_c']-12., .1)*fan
            expected += (.3348153529570458*cooling_kw + .17303382912664592*fan**3)/60
            assert current['electricity_kwh'] == pytest.approx(expected)
        assert policy['electricity_kwh'] == pytest.approx(expected)
    assert plan_result['estimated_saving_percent'] == pytest.approx(
        100*(1-plan_result['policies']['recommended']['electricity_kwh']/plan_result['policies']['regular']['electricity_kwh']))


def test_warm_start_not_erased_to_claim_equal_comfort():
    plan = cooling_plan({**VALUES, 'room_c': 28., 'outdoor_c': 36., 'solar_kw_m2': .6})
    assert not plan['comparable_comfort']
    assert plan['estimated_saving_percent'] is None
    assert plan['policies']['recommended']['series'][0]['room_c'] == 28.
    assert len(plan['policies']['recommended']['series']) == 61


@pytest.mark.parametrize('changes', [{'lower_c':25., 'upper_c':26.}, {'lower_c':26., 'upper_c':24.}])
def test_narrow_or_reversed_comfort_rejected(changes):
    with pytest.raises(ValueError, match='相差超过1'):
        cooling_plan({**VALUES, **changes})


def test_heating_not_counted_as_air_conditioner_electricity():
    with pytest.raises(ValueError, match='涉及供热'):
        cooling_plan({**VALUES, 'room_c': 18., 'outdoor_c': 0.})
