"""Interactive cooling estimates using the existing frozen model, not field evidence."""
import numpy as np

from app.algorithms.hvac_energy_optimizer import EnergyOptimizer, advance, forcing, actuator, predicted_power


def cooling_plan(values):
    room, lower, upper = values['room_c'], values['lower_c'], values['upper_c']
    if upper-lower <= 1:
        raise ValueError('舒适温度上下限需相差超过1℃，例如23—26℃。')
    drivers = np.array([values['outdoor_c'], 0., values['internal_kw']/2,
                        values['internal_kw']/2, 0., values['solar_kw_m2']/2,
                        values['solar_kw_m2']], dtype=float)
    forecast = np.tile(drivers, (60, 1))
    bounds = (np.full(61, lower), np.full(61, upper))
    policies = {}
    for name, policy in [('recommended', 'hybrid'), ('regular', 'simple')]:
        control = EnergyOptimizer(policy=policy)
        control.initialize(room)
        state = np.full(3, room, dtype=float)
        total = 0.
        series = [{'minute': 0, 'room_c': room, 'electricity_kwh': 0., 'fan_fraction': 0.}]
        first = None
        for minute in range(60):
            result = control.decide(float(state[0]), forecast, *bounds)
            if not result.get('command_valid') or not result['solver_success']:
                raise ValueError('当前条件下未得到可用方案，请调整舒适温度范围后重试。')
            target = result['target_net_heat_kw']
            if target > 1e-5:
                raise ValueError('当前条件涉及供热，本页仅支持制冷试算。请提高当前室温，或选择夏日示例。')
            command = actuator(target, state[0])
            # Use the delivered, capacity-limited cooling action in BOTH energy and dynamics.
            delivered = -.55*1.006*max(state[0]-12, .1)*command['fan_fraction']
            power, _ = predicted_power(delivered, state[0])  # No gas: cooling-only scope.
            total += power/60
            if first is None:
                first = {'fan_fraction': command['fan_fraction'],
                         'action': 'cool' if delivered < -1e-5 else 'idle'}
            past = drivers.copy()
            past[1] = delivered
            state = advance(state, forcing(past))
            if not np.isfinite(state).all():
                raise ValueError('当前条件超出示例模型的可计算范围，请更换夏日示例。')
            control.observe(float(state[0]), past)
            series.append({'minute': minute+1, 'room_c': float(state[0]),
                           'electricity_kwh': float(total), 'fan_fraction': command['fan_fraction']})
        temperatures = np.array([p['room_c'] for p in series])
        in_band = (temperatures >= lower-1e-6) & (temperatures <= upper+1e-6)
        policies[name] = {'series': series, 'electricity_kwh': float(total),
                          'in_range_minutes': int(in_band[1:].sum()),
                          'all_temperatures_in_range': bool(in_band.all()),
                          'minimum_room_c': float(temperatures.min()),
                          'maximum_room_c': float(temperatures.max()), 'first_action': first}
    regular, recommended = policies['regular'], policies['recommended']
    comparable = regular['all_temperatures_in_range'] and recommended['all_temperatures_in_range']
    difference = regular['electricity_kwh']-recommended['electricity_kwh']
    saving = 100*difference/regular['electricity_kwh'] if comparable and regular['electricity_kwh'] > 1e-6 else None
    return {'kind': 'cooling_plan_60m', 'scope': 'frozen_model_estimate_only', 'duration_minutes': 60,
            'inputs': dict(values), 'policies': policies, 'comparable_comfort': bool(comparable),
            'electricity_difference_kwh': float(difference), 'estimated_saving_percent': saving,
            'interpretation': '固定示例房间、恒定天气和活动条件下，同一简化模型内的预计对比；不是独立物理仿真或真实设备执行结果。'}
