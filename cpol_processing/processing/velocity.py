"""
Codes for correcting Doppler velocity.

@title: velocity
@author: Valentin Louf <valentin.louf@monash.edu>
@institutions: Monash University and the Australian Bureau of Meteorology
@creation: 11/12/2017
@date: 11/12/2017

.. autosummary::
    :toctree: generated/

    corr_velocity_from_phidp_artifacts
    correct_velocity_unfolding
    get_simulated_wind_profile
    unfold_velocity
"""

# Python Standard Library
from copy import deepcopy

# Other Libraries
import pyart
import netCDF4
import numpy as np

from netCDF4 import num2date
from unravel.dealias import process_3D


def check_nyquist_velocity(radar, vel_name='VEL'):
    """
    Check if Nyquist velocity is present in the instrument parameters. If not,
    then it is created.
    """
    try:
        radar.instrument_parameters['nyquist_velocity']
    except KeyError:
        vnyq = np.nanmax(radar.fields[vel_name]['data'])
        nray = len(radar.azimuth['data'])
        vnyq_array = np.array([vnyq] * nray, dtype=np.float32)
        nyquist_velocity = pyart.config.get_metadata('nyquist_velocity')
        nyquist_velocity['data'] = vnyq_array
        nyquist_velocity['_Least_significant_digit'] = 2
        radar.instrument_parameters['nyquist_velocity'] = nyquist_velocity

    return None


def unravel(radar, gatefilter, vel_name='VEL', dbz_name='DBZ'):
    """
    Unfold Doppler velocity using Py-ART region based algorithm. Automatically
    searches for a folding-corrected velocity field.

    Parameters:
    ===========
    radar:
        Py-ART radar structure.
    gatefilter:
        GateFilter
    vel_name: str
        Name of the (original) Doppler velocity field.
    dbz_name: str
        Name of the reflecitivity field.

    Returns:
    ========
    vel_meta: dict
        Unfolded Doppler velocity.
    """
    unfvel = process_3D(radar, velname=vel_name, dbzname=dbz_name, do_3D=False)
    np.ma.set_fill_value(unfvel, np.NaN)
    vel_meta = pyart.config.get_metadata('velocity')
    vel_meta['data'] = unfvel.astype(np.float32)
    vel_meta['_Least_significant_digit'] = 2
    vel_meta['_FillValue'] = np.NaN
    vel_meta['comment'] = 'UNRAVEL algorithm.'
    vel_meta['units'] = 'm/s'

    return vel_meta


def correct_velocity_unfolding(radar, vel_name="VEL_UNFOLDED", simvel_name="sim_velocity"):
    """
    Use radiosounding to constrain to the dominant wind the dealiased velocity.

    Parameters:
    ===========
    radar:
    vel_name: str
        Name of the unfolded velocity field
    simvel_name: str
        Name of the simulated wind velocity from the radiosounding.

    Returns:
    ========
    velmeta: dict
        Dictionnary containing the corrected dealiased velocity.
    """
    # Get data
    vel = radar.fields[vel_name]['data']
    newvels = radar.fields[vel_name]['data'].copy()
    simvel = radar.fields[simvel_name]['data']
    try:
        vnyq = radar.get_nyquist_vel(0)
    except Exception:
        vnyq = np.max(np.abs(vel))

    # Find wrongly unfolded velocities
    fmin = lambda x: x - vnyq
    fmax = lambda x: x + vnyq

    posmin = vel < fmin(simvel)
    posmax = vel > fmax(simvel)

    # Correct the wrong velocities.
    newvels[posmin] += vnyq * 2
    newvels[posmax] -= vnyq * 2

    # Velocity metadata.
    velmeta = pyart.config.get_metadata("velocity")
    velmeta['units'] = "m/s"
    velmeta['standard_name'] = "radial_velocity"
    velmeta['data'] = newvels

    return velmeta


def get_simulated_wind_profile(radar, radiosonde_fname, height_name="height", speed_name="wspeed", wdir_name="wdir"):
    """
    Simulate the horizontal wind profile for the radar.

    Parameters
    ==========
    radar:
        Py-ART radar data structure.
    radiosonde_fname: str
        Radiosonde file name.

    Returns:
    ========
    sim_vel: dict
        Simulated velocity.
    """
    interp_sonde = netCDF4.Dataset(radiosonde_fname)
    hwind_prof = pyart.core.HorizontalWindProfile(interp_sonde[height_name],
                                                  interp_sonde[speed_name],
                                                  interp_sonde[wdir_name],)
    sim_vel = pyart.util.simulated_vel_from_profile(radar, hwind_prof)
    try:
        sim_vel['units'] = "m/s"
        sim_vel['standard_name'] = "simulated_radial_velocity"
    except Exception:
        pass

    return sim_vel


def unfold_velocity(radar, my_gatefilter, bobby_params=False, constrain_sounding=False,
                    vel_name='VEL', rhohv_name='RHOHV_CORR', sounding_name='sim_velocity'):
    """
    Unfold Doppler velocity using Py-ART region based algorithm. Automatically
    searches for a folding-corrected velocity field.

    Parameters:
    ===========
        radar:
            Py-ART radar structure.
        my_gatefilter:
            GateFilter
        bobby_params: bool
            Using dealiasing parameters from Bobby Jackson. Otherwise using
            defaults configuration.
        constrain_sounding: bool
            Use optimization to constrain wind field according to a sounding. Useful if
            radar scan has regions overcorrected by a Nyquist interval.
        vel_name: str
            Name of the (original) Doppler velocity field.
        sounding_name: str
            Name of the wind field derived from a sounding

    Returns:
    ========
        vdop_vel: dict
            Unfolded Doppler velocity.
    """
    # Minimize cost function that is sum of difference between regions and
    def cost_function(nyq_vector):
        cost = 0
        i = 0
        for reg in np.unique(regions[sweep_slice]):
            add_value = np.abs(np.ma.mean(vels_slice[regions[sweep_slice] == reg]) + nyq_vector[i] * v_nyq_vel -
                               np.ma.mean(svels_slice[regions[sweep_slice] == reg]))

            if np.isfinite(add_value):
                cost += add_value
            i = i + 1
        return cost

    def gradient(nyq_vector):
        gradient_vector = np.zeros(len(nyq_vector))
        i = 0

        for reg in np.unique(regions[sweep_slice]):
            add_value = (np.ma.mean(vels_slice[regions[sweep_slice] == reg]) + nyq_vector[i] * v_nyq_vel -
                         np.ma.mean(svels_slice[regions[sweep_slice] == reg]))
            if(add_value > 0):
                gradient_vector[i] = v_nyq_vel
            else:
                gradient_vector[i] = -v_nyq_vel
            i = i + 1
        return gradient_vector

    gf = deepcopy(my_gatefilter)
    # Trying to determine Nyquist velocity
    try:
        v_nyq_vel = radar.instrument_parameters['nyquist_velocity']['data'][0]
    except Exception:
        vdop_art = radar.fields[vel_name]['data']
        v_nyq_vel = np.max(np.abs(vdop_art))

    try:
        vdop_vel = pyart.correct.dealias_region_based(radar, vel_field=vel_name, rays_wrap_around=True,
                                                      gatefilter=gf, skip_between_rays=2000)
    except Exception:
        vdop_vel = pyart.correct.dealias_region_based(radar, vel_field=vel_name,
                                                      gatefilter=gf, nyquist_vel=v_nyq_vel)

    # # Cf. mail from Bobby Jackson
    if constrain_sounding:
        # Import fmin_l_bfgs_b
        from scipy.optimize import fmin_l_bfgs_b

        gfilter = gf.gate_excluded
        vels = deepcopy(vdop_vel['data'])
        vels_uncorr = radar.fields[vel_name]['data']
        sim_vels = radar.fields[sounding_name]['data']
        diff = (sim_vels - vels) / v_nyq_vel
        region_means = []
        regions = np.zeros(vels.shape)

        for nsweep, sweep_slice in enumerate(radar.iter_slice()):
            sfilter = gfilter[sweep_slice]
            diffs_slice = diff[sweep_slice]
            vels_slice = vels[sweep_slice]
            svels_slice = sim_vels[sweep_slice]
            vels_uncorrs = vels_uncorr[sweep_slice]
            valid_sdata = vels_uncorrs[~sfilter]
            int_splits = pyart.correct.region_dealias._find_sweep_interval_splits(
                v_nyq_vel, 3, valid_sdata, nsweep)
            regions[sweep_slice], nfeatures = pyart.correct.region_dealias._find_regions(vels_uncorrs, sfilter,
                                                                                         limits=int_splits)

        bounds_list = [(x, y) for (x, y) in zip(-5 * np.ones(nfeatures + 1), 5 * np.ones(nfeatures + 1))]
        nyq_adjustments = fmin_l_bfgs_b(cost_function, np.zeros((nfeatures + 1)), disp=True, fprime=gradient,
                                        bounds=bounds_list, maxiter=20)
        i = 0
        for reg in np.unique(regions[sweep_slice]):
            reg_mean = np.mean(diffs_slice[regions[sweep_slice] == reg])
            region_means.append(reg_mean)
            vels_slice[regions[sweep_slice] == reg] += v_nyq_vel * np.round(nyq_adjustments[0][i])
            i = i + 1

        vels[sweep_slice] = vels_slice
        vdop_vel['data'] = vels

    vdop_vel['units'] = "m/s"
    vdop_vel['standard_name'] = "corrected_radial_velocity"
    vdop_vel['description'] = "Velocity unfolded using Py-ART region based dealiasing algorithm."

    return vdop_vel
