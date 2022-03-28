"""
Copyright (C) 2020-2022 Benjamin Bokser
"""
import plots
import mpc_cvx

# import time
# import sys
import numpy as np
import copy
from scipy.linalg import expm
import itertools

np.set_printoptions(suppress=True, linewidth=np.nan)


class Runner:
    def __init__(self, dims=2, ctrl='mpc', dt=1e-3):
        self.dims = dims
        self.ctrl = ctrl
        self.dt = dt
        self.total_run = 10000
        self.tol = 1e-3  # desired mpc tolerance
        self.hconst = 0.3
        self.m = 6  # mass of the robot
        self.N = 10  # mpc horizon length
        self.g = 9.81  # gravitational acceleration, m/s2
        self.t_p = 1  # gait period, seconds
        self.phi_switch = 0.5  # switching phase, must be between 0 and 1. Percentage of gait spent in contact.
        # for now, mpc sampling time is equal to gait period
        self.mpc_t = self.t_p * self.phi_switch  # mpc sampling time
        self.N_time = self.N*self.mpc_t  # mpc horizon time
        if dims == 2:
            self.n_x = 5  # number of states
            self.n_u = 2  # number of controls
            self.A = np.array([[0, 0, 1, 0, 0],
                               [0, 0, 0, 1, 0],
                               [0, 0, 0, 0, 0],
                               [0, 0, 0, 0, -1],
                               [0, 0, 0, 0, 0]])
            self.B = np.array([[0, 0],
                               [0, 0],
                               [1 / self.m, 0],
                               [0, 1 / self.m],
                               [0, 0]])
            self.pos_ref = np.array([1, 1])  # desired body position in world coords
            self.vel_ref = np.array([0, 0])  # desired body velocity in world coords

        elif dims == 3:
            self.n_x = 7  # number of states
            self.n_u = 3  # number of controls
            self.A = np.array([[0, 0, 0, 1, 0, 0, 0],
                               [0, 0, 0, 0, 1, 0, 0],
                               [0, 0, 0, 0, 0, 1, 0],
                               [0, 0, 0, 0, 0, 0, 0],
                               [0, 0, 0, 0, 0, 0, 0],
                               [0, 0, 0, 0, 0, 0, -1],
                               [0, 0, 0, 0, 0, 0, 0]])
            self.B = np.array([[0, 0, 0],
                               [0, 0, 0],
                               [0, 0, 0],
                               [1 / self.m, 0, 0],
                               [0, 1 / self.m, 0],
                               [0, 0, 1 / self.m],
                               [0, 0, 0]])
            self.pos_ref = np.array([1, 1, 1])  # desired body position in world coords
            self.vel_ref = np.array([0, 0, 0])  # desired body velocity in world coords

        self.X_0 = np.zeros(self.n_x)
        self.X_0[-1] = self.g  # initial conditions
        self.X_f = np.hstack([self.pos_ref, self.vel_ref, self.g]).T  # desired final state
        mu = 0.3  # coeff of friction
        self.mpc = mpc_cvx.Mpc(t=self.mpc_t, A=self.A, B=self.B, N=self.N, m=self.m, g=self.g, mu=mu)
        self.mpc_factor = self.mpc_t / self.dt  # repeat mpc every x seconds

    def run(self):
        total = self.total_run + 1  # number of timesteps to plot
        t = 0  # time
        t0 = t  # starting time

        mpc_factor = self.mpc_factor  # repeat mpc every x seconds
        mpc_counter = copy.copy(mpc_factor)
        force_f = None
        X_traj = np.zeros((total, self.n_x))
        X_traj[0, -1] = self.g  # initial conditions
        f_hist = np.zeros((total, self.n_u))
        s_hist = np.zeros((total, 2))
        sh = 0

        for k in range(0, self.total_run):
            t = t + self.dt

            s = self.gait_scheduler(t, t0)

            if self.ctrl == 'mpc':
                if mpc_counter == mpc_factor:  # check if it's time to restart the mpc
                    mpc_counter = 0  # restart the mpc counter
                    X_ref = self.path_plan(X_in=X_traj[k, :])
                    X_refN = X_ref[::int(self.mpc_factor)]  # self.traj_N(X_ref)
                    force_f, sh = self.mpc.mpcontrol(X_in=X_traj[k, :], X_ref=X_refN, s=s)
                mpc_counter += 1
                f_hist[k, :] = force_f[:, 0]  # take first timestep

            else:  # Open loop traj opt, this will fail if total != mpc_factor
                if k == 0:
                    X_ref = self.path_plan(X_in=X_traj[k, :])
                    X_refN = X_ref[::int(self.mpc_factor)]  # self.traj_N(X_ref)
                    force_f, sh = self.mpc.mpcontrol(X_in=X_traj[k, :], X_ref=X_refN, s=s)
                    j = int(self.mpc_factor)
                    # j = int(self.total_run/self.N)
                    for i in range(0, self.N):
                        f_hist[int(i*j):int(i*j+j), :] = list(itertools.repeat(force_f[:, i], j))

            s_hist[k, :] = [s, sh]
            X_traj[k+1, :] = self.rk4(xk=X_traj[k, :], uk=f_hist[k, :])
            # X_traj[k + 1, :] = self.dynamics_dt(X=X_traj[k, :], U=f_hist[k, :])

        # print(X_traj[-1, :])
        # print(f_hist[4500, :])
        plots.fplot(total, p_hist=X_traj[:, 0:self.n_u], f_hist=f_hist, s_hist=s_hist, dims=self.dims)
        plots.posplot(p_ref=self.X_f[0:self.n_u], p_hist=X_traj[:, 0:self.n_u], dims=self.dims)
        # plots.posplot_t(p_ref=self.X_ref[0:self.n_u], p_hist=X_traj[:, 0:2], total=total)

        return None

    def dynamics_ct(self, X, U):
        # CT dynamics X -> dX
        A = self.A
        B = self.B
        X_next = A @ X + B @ U
        return X_next

    def dynamics_dt(self, X, U):
        t = self.dt
        n_x = self.n_x  # number of states
        n_u = self.n_u  # number of controls
        A = self.A
        B = self.B
        AB = np.vstack((np.hstack((A, B)), np.zeros((n_u, n_x + n_u))))
        M = expm(AB * t)
        Ad = M[0:n_x, 0:n_x]
        Bd = M[0:n_x, n_x:n_x + n_u]
        X_next = Ad @ X + Bd @ U
        return X_next

    def rk4(self, xk, uk):
        # RK4 integrator solves for new X
        dynamics = self.dynamics_ct
        h = self.dt
        f1 = dynamics(xk, uk)
        f2 = dynamics(xk + 0.5 * h * f1, uk)
        f3 = dynamics(xk + 0.5 * h * f2, uk)
        f4 = dynamics(xk + h * f3, uk)
        return xk + (h / 6.0) * (f1 + 2 * f2 + 2 * f3 + f4)

    def gait_scheduler(self, t, t0):
        phi = np.mod((t - t0) / self.t_p, 1)
        if phi > self.phi_switch:
            s = 0  # scheduled swing
        else:
            s = 1  # scheduled stance
        return s

    def path_plan(self, X_in):
        # Path planner--generate reference trajectory
        dt = self.dt
        size_mpc = int(self.mpc_factor*self.N)  # length of MPC horizon in s TODO: Perhaps N should vary wrt time?
        t_ref = 0  # timesteps given to get to target, either mpc length or based on distance (whichever is smaller)
        if self.dims == 2:
            t_ref = int(np.minimum(size_mpc, abs(self.X_f[0] - X_in[0])*1000))  # ignore z distance due to bouncing
            X_ref = np.linspace(start=X_in, stop=self.X_f, num=t_ref)  # interpolate positions
            # interpolate velocities
            X_ref[:-1, 2] = [(X_ref[i + 1, 0] - X_ref[i, 0]) / dt for i in range(0, np.shape(X_ref)[0] - 1)]
            X_ref[:-1, 3] = [(X_ref[i + 1, 1] - X_ref[i, 1]) / dt for i in range(0, np.shape(X_ref)[0] - 1)]
        elif self.dims == 3:
            t_ref = int(np.minimum(size_mpc, np.linalg.norm(self.X_f[0:2] - X_in[0:2]) * 1000))
            X_ref = np.linspace(start=X_in, stop=self.X_f, num=t_ref)  # interpolate positions
            # interpolate velocities
            X_ref[:-1, 3] = [(X_ref[i + 1, 0] - X_ref[i, 0]) / dt for i in range(0, np.shape(X_ref)[0] - 1)]
            X_ref[:-1, 4] = [(X_ref[i + 1, 1] - X_ref[i, 1]) / dt for i in range(0, np.shape(X_ref)[0] - 1)]
            X_ref[:-1, 5] = [(X_ref[i + 1, 2] - X_ref[i, 2]) / dt for i in range(0, np.shape(X_ref)[0] - 1)]

        if (size_mpc - t_ref) == 0:
            pass
        elif t_ref == 0:
            X_ref = np.array(list(itertools.repeat(self.X_f, int(size_mpc))))
        else:
            X_ref = np.vstack((X_ref, list(itertools.repeat(self.X_f, int(size_mpc - t_ref)))))

        return X_ref

