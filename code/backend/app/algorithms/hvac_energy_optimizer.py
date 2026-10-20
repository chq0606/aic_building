"""Standalone final-development HVAC optimizer for the public bestest_air case.

Requires numpy/scipy; coefficients below are specific to this public physical case.
No filesystem, old project imports, emulator states or future measured room inputs.
"""
import numpy as np
from scipy.linalg import expm
from scipy.optimize import linprog

# Frozen joint_model_fit08 physical parameters, in its recorded order.
P = np.array([.04255388553983925, .020000004899182133, 2.773311018707472,
              30., 5.775314383312937, 20.47526157701798, .0446998031623682,
              1.1102354805436156, 8.043456727687236, 4.61263916619489,
              1.9018122837167515e-26, .14437161668450357, 1.598755214120179e-25,
              .0001, .2219976524437495])


def forcing(features):
    """Features [Tout,Qnet,Iconv,Irad,south_direct,diffuse,GHI], kW and C."""
    w = np.asarray(features)
    solar = w[4:] @ P[8:11]
    return np.array([w[0], w[1]+P[7]*w[2], P[7]*P[12]*w[3]+P[11]*solar,
                     P[7]*(1-P[12])*w[3]+(1-P[11])*solar])


def rates(x, f):
    a, m, e = x
    ca, cm, ce, ram, rae, reo, gao = P[:7]
    dm, de = a-m, a-e
    qm = dm/ram*(dm*dm+.01)**(1/6)
    qe = de/rae*(de*de+.01)**(1/6)
    return np.array([(gao*(f[0]-a)-qm-qe+f[1])/ca,
                     (qm+f[2])/cm, (qe+(f[0]-e)/reo+f[3])/ce])


def advance(x, f, minutes=1):
    x = np.asarray(x).copy()
    for _ in range(4*minutes):
        x += rates(x+rates(x, f)/480, f)/240
    return x


def jacobian(x):
    a, m, e = x
    ca, cm, ce, ram, rae, reo, gao = P[:7]
    def conductance(delta, resistance):
        return ((delta*delta+.01)**(1/6)+delta*delta/3*(delta*delta+.01)**(-5/6))/resistance
    gm, ge = conductance(a-m, ram), conductance(a-e, rae)
    return np.array([[-(gm+ge+gao)/ca, gm/ca, ge/ca],
                     [gm/cm, -gm/cm, 0], [ge/ce, 0, -(ge+1/reo)/ce]])


class JointController:
    def __init__(self, policy='mpc', margin_c=.50):
        assert policy in ('mpc', 'simple')
        self.policy = policy
        self.margin = margin_c
        self.state = None
        self.previous_plan = np.zeros(12)

    def initialize(self, room_c):
        self.state = np.full(3, float(room_c))

    def observe(self, measured_room_c, past_features):
        predicted = advance(self.state, forcing(past_features))
        error = measured_room_c-predicted[0]
        self.state = np.array([measured_room_c, predicted[1]+P[13]*error, predicted[2]+P[14]*error])
        assert np.isfinite(self.state).all()

    def decide(self, room_c, forecast_features, lower_c, upper_c):
        """Minute forecasts61 boundaries; 60 future forcing means. Q column overwritten.

        Known external/schedule forecasts only. Bounds are source comfort limits,
        never widened. Returned Q is a signed sensible heat target in kW.
        """
        self.state[0] = room_c
        forecast = np.asarray(forecast_features, dtype=float).copy()
        assert forecast.shape == (60, 7)
        assert len(lower_c) == len(upper_c) == 61
        forecast[:, 1] = 0
        if self.policy == 'simple':
            lower = max(lower_c[:31])+self.margin
            upper = min(upper_c[:31])-self.margin
            free = rates(self.state, forcing(forecast[0]))[0]
            qsteady = -free*P[0]
            if room_c < lower+.1:
                q = max(qsteady+3*(lower-room_c), 0.)
            elif room_c > upper-.1:
                q = min(qsteady+3*(upper-room_c), 0.)
            else:
                q = 0.
            return {'target_net_heat_kw': float(np.clip(q, -6, 6)), 'mode': 'simple',
                    'predicted_slack_c': 0., 'solver_success': True, 'simultaneous_heat_cool_kw': 0.}
        n = 12
        lower = np.asarray(lower_c[1:])+self.margin
        upper = np.asarray(upper_c[1:])-self.margin
        nominal = self.previous_plan.copy()
        last = None
        for _ in range(2):
            state = self.state.copy()
            base = self.state.copy()
            sensitivity = np.zeros((3, n))
            base_air, gain, capacities = [], [], []
            for j in range(n):
                anchor = state.copy()
                a = jacobian(state)
                ad = expm(a/60)
                integral = np.linalg.solve(a, ad-np.eye(3))
                bq = integral @ np.array([1/P[0], 0, 0])
                capacities.append([.55*1.006*max(40-state[0], 1), .55*1.006*max(state[0]-12, 1)])
                for minute in range(5):
                    f = forcing(forecast[5*j+minute])
                    affine = rates(anchor, f)-a@anchor
                    base = ad@base+integral@affine
                    sensitivity = ad@sensitivity
                    sensitivity[:, j] += bq
                    base_air.append(base[0])
                    gain.append(sensitivity[0].copy())
                    f[1] += nominal[j]
                    state = advance(state, f)
            base_air, gain, capacities = np.array(base_air), np.array(gain), np.array(capacities)
            # Variables: heat12,cool12,fan_power12,low_slack60,high_slack60.
            identity = np.eye(n)
            zero_temp_control = np.zeros((60, n))
            zero_slack = np.zeros((60, 60))
            rows = [np.column_stack([-gain, gain, zero_temp_control, -np.eye(60), zero_slack]),
                    np.column_stack([gain, -gain, zero_temp_control, zero_slack, -np.eye(60)])]
            rhs = [base_air-lower, upper-base_air]
            flow = np.column_stack([np.diag(1/capacities[:, 0]), np.diag(1/capacities[:, 1]), np.zeros((n,n+120))])
            rows.append(flow)
            rhs.append(np.ones(n))
            for left, right in zip(np.arange(0, 1, .2), np.arange(.2, 1.01, .2)):
                slope = .17303382912664592*(right**3-left**3)/(right-left)
                intercept = .17303382912664592*left**3-slope*left
                plane = slope*flow.copy()
                plane[:, 2*n:3*n] = -identity
                rows.append(plane)
                rhs.append(np.full(n, -intercept))
            objective = np.r_[np.full(n, 1.1144758790510654/12), np.full(n, .3348153529570458/12),
                               np.full(n, 1/12), np.full(120, 1e4)]
            result = linprog(objective, A_ub=np.vstack(rows), b_ub=np.concatenate(rhs),
                             bounds=[(0, 6)]*(2*n)+[(0, None)]*(n+120), method='highs')
            if not result.success:
                return {'target_net_heat_kw': 0., 'mode': 'solver_failure', 'predicted_slack_c': None,
                        'solver_success': False, 'solver_message': result.message, 'simultaneous_heat_cool_kw': None}
            nominal = result.x[:n]-result.x[n:2*n]
            last = result
        self.previous_plan = nominal
        return {'target_net_heat_kw': float(nominal[0]), 'mode': 'mpc',
                'predicted_slack_c': float(last.x[3*n:].max()), 'solver_success': True,
                'simultaneous_heat_cool_kw': float(np.minimum(last.x[:n], last.x[n:2*n]).max()),
                'surrogate_objective_kwh_plus_slack_penalty': float(last.fun)}


# Frozen v6 equilibrium/PI coefficients, reused without fitting on new dates.
COOLING_COEFFICIENTS = {
    'room_center': 1.0545466086400894, 'previous_room_center': -.1562310162427635,
    'outdoor_center': .006547981277014218, 'lag_outdoor_center': .02531504421679621,
    'solar_kw_m2': 2.391435293293338e-13, 'lag_solar_kw_m2': .23374000127131728,
    'internal_kw': 1.1310519949690763, 'lag_internal_kw': -.8675728651698319,
    'cooling_kw': -2.1290740598325955, 'lag_cooling_kw': 1.8013372839863728,
    'intercept': .06002189611241664,
}
DEFAULT_CANDIDATE = 'A'


def cooling_step(room, reference, features, integral):
    c = COOLING_COEFFICIENTS
    balance = (c['room_center']+c['previous_room_center']-1)*(reference-24)+c['intercept']
    driver = {'outdoor_center': features[0]-24, 'solar_kw_m2': features[6],
              'internal_kw': features[2]+features[3]}
    for key, value in driver.items():
        balance += (c[key]+c['lag_'+key])*value
    feedforward = max(0., -balance/(c['cooling_kw']+c['lag_cooling_kw']))
    error = room-reference
    raw = feedforward+3*error+integral
    if not ((raw >= 6 and error > 0) or (raw <= 0 and error < 0)):
        integral = float(np.clip(integral+.08*error, -6, 6))
    return float(np.clip(feedforward+3*error+integral, 0, 6)), integral, feedforward


def actuator(target, room):
    supply = 40. if target > 0 else 12.
    capacity = .55*1.006*max(abs(supply-room), .1)
    required = abs(target)/capacity
    return {'fan_fraction': float(np.clip(required, 0, 1)), 'supply_temperature_c': supply,
            'capacity_clipped': bool(required > 1+1e-8)}


def predicted_power(target, room):
    command = actuator(target, room)
    electric = .3348153529570458*max(-target, 0)+.17303382912664592*command['fan_fraction']**3
    gas = 1.1144758790510654*max(target, 0)
    return electric+gas, .6618*electric+.18108*gas


class EnergyOptimizer(JointController):
    """Nominal predictive supervisor, NOT a formally certified safety filter.

    observe() takes past realized inputs; decide() takes external forecasts and
    source comfort bounds. Keep one instance per continuous room experiment.
    """
    def __init__(self, policy='hybrid', candidate=DEFAULT_CANDIDATE):
        if policy not in ('hybrid', 'mpc', 'simple') or candidate not in ('A', 'B'):
            raise ValueError('Unknown fixed policy/candidate')
        super().__init__('simple' if policy == 'simple' else 'mpc')
        self.strategy = policy
        self.lower_buffer, self.upper_buffer = {'A': (.4, .1), 'B': (.6, .2)}[candidate]
        self.cooling_active = False
        self.integral = 0.
        self.last_delivered_heat_kw = 0.
        self.eligible_minutes = 0

    def observe(self, measured_room_c, past_features):
        if self.state is None:
            raise ValueError('initialize() and past-data assimilation are required')
        if not np.isfinite(measured_room_c) or np.asarray(past_features).shape != (7,) or not np.isfinite(past_features).all():
            raise ValueError('Past measured room and seven drivers must be finite')
        super().observe(measured_room_c, past_features)
        self.last_delivered_heat_kw = float(past_features[1])

    def _rollout(self, forecast, lower, upper, cooling, initial_integral=0.):
        state = self.state.copy()
        energy = carbon = 0.
        min_lower = min_upper = float('inf')
        integral = initial_integral
        first_target = first_integral = 0.
        for minute in range(60):
            if cooling:
                reference = float(min(upper[minute:])-.10)
                preview = forecast[minute:min(minute+15, 60)].mean(axis=0)
                magnitude, integral, _ = cooling_step(state[0], reference, preview, integral)
                target = -min(magnitude, .55*1.006*max(state[0]-12, .1))
                if minute == 0:
                    first_target, first_integral = target, integral
            else:
                target = float(self.previous_plan[minute//5])
            terminal, emissions = predicted_power(target, state[0])
            energy += terminal/60
            carbon += emissions/60
            features = forecast[minute].copy()
            features[1] = target
            state = advance(state, forcing(features))
            min_lower = min(min_lower, state[0]-lower[minute+1])
            min_upper = min(min_upper, upper[minute+1]-state[0])
        return {'energy_kwh': float(energy), 'carbon_kg': float(carbon), 'min_lower_gap_c': float(min_lower),
                'min_upper_gap_c': float(min_upper), 'first_target': float(first_target), 'first_integral': float(first_integral)}

    def decide(self, room_c, forecast_features, lower_c, upper_c):
        forecast = np.asarray(forecast_features, dtype=float)
        lower, upper = np.asarray(lower_c, dtype=float), np.asarray(upper_c, dtype=float)
        if self.state is None or forecast.shape != (60, 7) or lower.shape != (61,) or upper.shape != (61,):
            raise ValueError('Initialize state; supply60x7 drivers and61 lower/upper bounds')
        if not all(np.isfinite(value).all() for value in (room_c, forecast, lower, upper)) or not (lower < upper).all():
            raise ValueError('Finite measurements/forecasts and ordered comfort bounds are required')
        base = super().decide(float(room_c), forecast, lower, upper)
        base.update(selected_controller=self.strategy if self.strategy != 'hybrid' else 'mpc', switched=False)
        if not base['solver_success']:
            base.update(command_valid=False)
            return base
        if self.strategy != 'hybrid':
            base.update(actuator(base['target_net_heat_kw'], room_c), command_valid=True)
            return base
        was_cooling = self.cooling_active
        if room_c < lower[0]+self.lower_buffer or np.max(self.previous_plan) > 1e-6:
            self.cooling_active = False
            self.eligible_minutes = 0
            base.update(selected_controller='mpc', switched=was_cooling,
                        cooling_prediction_eligible=False, selection_reason='predicted heating need or measured lower-bound proximity',
                        command_valid=True)
            base.update(actuator(base['target_net_heat_kw'], room_c))
            return base
        reference = float(min(upper)-.10)
        _, _, feedforward = cooling_step(room_c, reference, forecast[:15].mean(axis=0), 0.)
        seed = self.integral if was_cooling else float(np.clip(max(-self.last_delivered_heat_kw, 0)-feedforward-3*(room_c-reference), -6, 6))
        cheap = self._rollout(forecast, lower, upper, True, seed)
        backup = self._rollout(forecast, lower, upper, False)
        risk_ok = (room_c >= lower[0]+self.lower_buffer and cheap['min_lower_gap_c'] >= self.lower_buffer
                   and cheap['min_upper_gap_c'] >= self.upper_buffer)
        ratio = 1.005 if was_cooling else .995
        economical = cheap['energy_kwh'] <= backup['energy_kwh']*ratio and cheap['carbon_kg'] <= backup['carbon_kg']+1e-9
        self.eligible_minutes = self.eligible_minutes+1 if risk_ok and economical else 0
        self.cooling_active = bool(risk_ok and economical and (was_cooling or self.eligible_minutes >= 3))
        if self.cooling_active:
            self.integral = cheap['first_integral']
            base.update(target_net_heat_kw=cheap['first_target'], selected_controller='cooling_ffpi')
        base.update(switched=was_cooling != self.cooling_active, cooling_prediction_eligible=bool(risk_ok),
                    eligible_minutes=self.eligible_minutes, predicted_cooling_energy_kwh=cheap['energy_kwh'],
                    predicted_mpc_energy_kwh=backup['energy_kwh'], predicted_cooling_lower_gap_c=cheap['min_lower_gap_c'],
                    predicted_cooling_upper_gap_c=cheap['min_upper_gap_c'],
                    prediction_guard='Finite nominal rollout, no invariant-set or bounded-uncertainty guarantee', command_valid=True)
        base.update(actuator(base['target_net_heat_kw'], room_c))
        return base
