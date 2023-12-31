import qnm
import scri
import numpy as np
import spherical_functions as sf
from scri.sample_waveforms import modes_constructor

from scipy.integrate import trapezoid
from scipy import linalg
from scipy.sparse import spdiags

_ksc = qnm.modes_cache


def waveform_mismatch(h_A, h_B, modes=None, t1=0, t2=100):
    """Compute the mismatch between two waveforms.
    Assumes that the waveforms have the same time arrays.

    Parameters
    ----------
    h_A : scri.WaveformModes
        One of the waveforms to use in the mismatch computation.
    h_B : scri.WaveformModes
        The other waveform to use in the mismatch computation.
    modes : list, optional
        list of modes, e.g., (ell, m), which will be included in the mismatch.
        Default is to use every mode.
    t1 : float, optional
        Lower boundary of times to include in the mismatch. [Default: 0.]
    t2 : float, optional
        Upper boundary of times to include in the mismatch. [Default: 100.]

    Returns
    -------
    mismatch : float
        Mismatch between the two waveforms.

    """
    h_A_copy = h_A.copy()
    h_B_copy = h_B.copy()
    if modes != None:
        for L, M in [
            (L_value, M_value)
            for L_value in range(2, h_A_copy.ell_max + 1)
            for M_value in range(-L_value, L_value + 1)
        ]:
            if not (L, M) in modes:
                h_A_copy.data[:, sf.LM_index(L, M, h_A_copy.ell_min)] *= 0
                h_B_copy.data[:, sf.LM_index(L, M, h_B_copy.ell_min)] *= 0

    h_A_copy = h_A_copy[np.argmin(abs(h_A.t - t1)) + 1 : np.argmin(abs(h_A.t - t2)) + 1]
    if h_A_copy.t.shape[0] != h_B_copy.t.shape[0]:
        h_B_copy = h_B_copy.interpolate(h_A_copy.t)
    else:
        h_B_copy = h_B_copy[
            np.argmin(abs(h_B.t - t1)) + 1 : np.argmin(abs(h_B.t - t2)) + 1
        ]
    return 1 - trapezoid(
        np.sum(np.real(h_A_copy.data * np.conjugate(h_B_copy.data)), axis=1), h_A_copy.t
    ) / np.sqrt(
        trapezoid(h_A_copy.norm(), h_A_copy.t) * trapezoid(h_B_copy.norm(), h_B_copy.t)
    )


def qnm_from_tuple(tup, chi, mass, s=-2):
    """Get frequency and spherical_spheroidal mixing from qnm module

    Parameters
    ----------
    tup : tuple
        Index (ell,m,n,sign) of QNM
    chi : float
        The dimensionless spin of the black hole, 0. <= chi < 1.
    mass : float
        The mass of the black hole, M > 0.
    s : int, optional [Default: -2]

    Returns
    -------
    omega: complex
        Frequency of QNM. This frequency is the same units as arguments,
        as opposed to being in units of remnant mass.
    C : complex ndarray
        Spherical-spheroidal decomposition coefficient array
    ells : ndarray
        List of ell values for the spherical-spheroidal mixing array

    """
    ell, m, n, sign = tup
    if sign == +1:
        mode_seq = _ksc(s, ell, m, n)
    elif sign == -1:
        mode_seq = _ksc(s, ell, -m, n)
    else:
        raise ValueError(
            "Last element of mode label must be "
            "+1 or -1, instead got {}".format(sign)
        )

    # The output from mode_seq is M*\omega
    try:
        Momega, _, C = mode_seq(chi, store=True)
    except:
        Momega, _, C = mode_seq(chi, interp_only=True)

    ells = qnm.angular.ells(s, m, mode_seq.l_max)

    if sign == -1:
        Momega = -np.conj(Momega)
        C = (-1) ** (ell + ells) * np.conj(C)

    # Convert from M*\omega to \omega
    omega = Momega / mass
    return omega, C, ells


def qnm_modes(chi, mass, mode_dict, dest=None, t_0=0.0, t_ref=0.0, **kwargs):
    """Convert a dictionary of QNMs to a scri.WaveformModes object.
    Additional keyword arguments are passed to `modes_constructor`.

    Parameters
    ----------
    chi : float
        The dimensionless spin of the black hole, 0. <= chi < 1.
    mass : float
        The mass of the black hole, M > 0.
    mode_dict : dict
        Dict with terms which are either QNM or other terms.
        Each term should be a dict with:
            - a 'type' (QNM or other),
            - a 'mode' (if 'type' == 'QNM')
              in the format (ell, m, n, sign)
            - a 'A'
            - a 'omega'
                - this is really only necessary if the QNM 'type'
                  is 'other', in which case this 'omega' is used as the frequency
    dest : ndarray, optional [Default: None]
        If passed, the storage to use for the scri.WaveformModes.data.
        Must be the correct shape.
    t_0 : float, optional [Default: 0.]
        Waveform model is 0 for t < t_0.
    t_ref : float, optional [Default: 0.]
        Time at which amplitudes are specified.

    Returns
    -------
    Q : scri.WaveformModes

    """
    s = -2

    ell_max = 12

    def data_functor(t, LM):
        d_shape = (t.shape[0], LM.shape[0])

        if dest is None:
            data = np.zeros(d_shape, dtype=complex)
        else:
            if (dest.shape != d_shape) or (dest.dtype is not np.dtype(complex)):
                raise TypeError("dest has wrong dtype or shape")
            data = dest
            data.fill(0.0)

        chi_is_array = type(chi) == list or type(chi) == np.ndarray
        mass_is_array = type(mass) == list or type(mass) == np.ndarray
        for term in mode_dict.values():
            if term["type"] == "QNM":
                if chi_is_array or mass_is_array:
                    for i in range(len(t)):
                        if chi_is_array:
                            chi_value = chi[i]
                        else:
                            chi_value = chi

                        if mass_is_array:
                            mass_value = mass[i]
                        else:
                            mass_value = mass

                        ell, m, n, sign = term["mode"]
                        omega, C, ells = qnm_from_tuple(
                            (ell, m, n, sign), chi_value, mass_value, s
                        )

                        A = term["A"]

                        expiwt = np.exp(complex(0.0, -1.0) * omega * (t[i] - t_ref))
                        for _l, _m in LM:
                            if _m == m:
                                c_l = C[ells == _l]
                                if len(c_l) > 0:
                                    c_l = c_l[0]

                                data[i, sf.LM_index(_l, _m, min(LM[:, 0]))] += (
                                    A * expiwt * c_l
                                )
                else:
                    ell, m, n, sign = term["mode"]
                    omega, C, ells = qnm_from_tuple((ell, m, n, sign), chi, mass, s)

                    A = term["A"]

                    expiwt = np.exp(complex(0.0, -1.0) * omega * (t - t_ref))
                    for _l, _m in LM:
                        if _m == m:
                            c_l = C[ells == _l]
                            if len(c_l) > 0:
                                c_l = c_l[0]

                            data[:, sf.LM_index(_l, _m, min(LM[:, 0]))] += (
                                A * expiwt * c_l
                            )
            elif term["type"] == "other":
                omega = term["omega"]

                A = term["A"]

                expiwt = np.exp(complex(0.0, -1.0) * omega * (t - t_ref))
                data[
                    :,
                    sf.LM_index(
                        term["target mode"][0], term["target mode"][1], min(LM[:, 0])
                    ),
                ] += (
                    A * expiwt
                )
            else:
                raise ValueError("QNM term type not recognized...")

        return data

    return modes_constructor(
        "qnm_modes({0}, {1}, {2}, t_0={3}, t_ref={4}, **{5})".format(
            chi, mass, mode_dict, t_0, t_ref, kwargs
        ),
        data_functor,
        **kwargs
    )


def qnm_modes_as(
    chi, mass, mode_dict, W_other, dest=None, t_0=0.0, t_ref=0.0, **kwargs
):
    """Convert a dictionary of QNMs to a scri.WaveformModes object.
    Will match the structure of W_other.
    Additional keyword arguments are passed to `modes_constructor`.

    Parameters
    ----------
    chi : float
        The dimensionless spin of the black hole, 0. <= chi < 1.
    mass : float
        The mass of the black hole, M > 0.
    mode_dict : dict
        Dict with terms which are either QNM or other terms.
        Each term should be a dict with:
            - a 'type' (QNM or other),
            - a 'mode' (if 'type' == 'QNM')
              in the format (ell, m, n, sign)
            - a 'A'
            - a 'omega'
                - this is really only necessary if the QNM 'type'
                  is 'other', in which case this 'omega' is used as the frequency
    W_other : scri.WaveformModes object
        Get the time and LM from this scri.WaveformModes object
    dest : ndarray, optional [Default: None]
        If passed, the storage to use for the scri.WaveformModes.data.
        Must be the correct shape.
    t_0 : float, optional [Default: 0.]
        Waveform model is 0 for t < t_0.
    t_ref : float, optional [Default: 0.]
        Time at which amplitudes are specified.

    Returns
    -------
    Q : scri.WaveformModes

    """
    t = W_other.t
    ell_min = W_other.ell_min
    ell_max = W_other.ell_max

    return qnm_modes(
        chi,
        mass,
        mode_dict,
        dest=dest,
        t_0=t_0,
        t_ref=t_ref,
        t=t,
        ell_min=ell_min,
        ell_max=ell_max,
        **kwargs
    )


def fit_ringdown_waveform_LLSQ_S2(
    h_ring, modes, times, chi_f, M_f, fixed_QNMs, t_ref=0
):
    """Linear least squares routine for fitting a NR waveform with QNMs.

    Paramters
    ---------
    h : scri.WaveformModes
        Input waveform to fit to.
    modes : list of tuples
        Mode of waveform, e.g., (ell, m), to fit to.
    times : tuple
        Times to fit over, e.g., (0, 100).
    chi_f : float
        The dimensionless spin of the black hole, 0. <= chi < 1.
    M_f : float
        The mass of the black hole, M > 0.
    fixed_QNMs : dict
        Dictionary of QNMs to fit.
        Each entry should look something like:
            {
              'type': 'QNM',
              'target mode': (2,2),
              'mode': (2,2,0,1),
              'omega': 0.5 - 0.1j
            }
    t_ref : float, optional [Default: 0.]
        Time at which amplitudes are specified.

    Returns
    -------
    h_QNM : scri.WaveformModes
        QNM waveform from fit.
    h_ring : scri.WaveformModes
        NR waveform that was fit to.
    error : float
        0.5 * L2norm(h_ring - h_QNM)^2 / L2norm(h_ring)^2
    mismatch : float
        Mismatch between h_ring and h_QNM.
    fixed_QNMs : dict
        Identical to input `fixed_QNMs`, but with the key 'A' describing the QNM amplitude from the fit.

    """
    t_0 = times[0]

    m_list = []
    [
        m_list.append(term["mode"][1])
        for term in fixed_QNMs.values()
        if term["mode"][1] not in m_list
    ]
    # break problem into one m at a time.
    # the m's are decoupled, and the truncation in ell for each m is different.
    for m in m_list:
        mode_labels_m = [
            (i, term)
            for i, term in enumerate(fixed_QNMs.values())
            if term["mode"][1] == m
        ]

        # restrict the modes included in the least squares fit to the modes of interest
        ell_min_m = h_ring.ell_min
        ell_max_m = h_ring.ell_max
        if modes is None:
            data_index_m = [
                sf.LM_index(l, m, h_ring.ell_min) for l in range(2, ell_max_m + 1)
            ]
        else:
            data_index_m = [
                sf.LM_index(l, m, h_ring.ell_min)
                for l in range(2, ell_max_m + 1)
                if (l, m) in modes
            ]
            ell_min_m = min(np.array([_l for (_l, _m) in modes if m == _m]))
            ell_max_m = max(np.array([_l for (_l, _m) in modes if m == _m]))

        A = np.zeros((len(h_ring.t), ell_max_m - ell_min_m + 1), dtype=complex)
        B = np.zeros(
            (len(h_ring.t), ell_max_m - ell_min_m + 1, len(mode_labels_m)),
            dtype=complex,
        )

        h_ring_trunc = h_ring[:, : ell_max_m + 1]
        A = h_ring_trunc.data[:, data_index_m]
        for mode_index, (i, term) in enumerate(mode_labels_m):
            term_w_A = term.copy()
            term["A"] = 1
            h_QNM = qnm_modes_as(
                chi_f, M_f, {0: term}, h_ring_trunc, t_0=t_0, t_ref=t_ref
            )
            B[:, :, mode_index] = h_QNM.data[:, data_index_m]

        A = np.reshape(A, len(h_ring.t) * (ell_max_m - ell_min_m + 1))
        B = np.reshape(
            B, (len(h_ring.t) * (ell_max_m - ell_min_m + 1), len(mode_labels_m))
        )
        C = np.linalg.lstsq(B, A, rcond=None)

        count = 0
        for i, term in mode_labels_m:
            fixed_QNMs[i]["A"] = C[0][count]
            count += 1

    h_QNM = qnm_modes_as(chi_f, M_f, fixed_QNMs, h_ring, t_0=t_0, t_ref=t_ref)

    h_diff = h_ring.copy()
    h_diff.data *= 0

    if modes is None:
        modes = [(L, M) for L in range(2, h_ring.ell_max + 1) for M in range(-L, L + 1)]

    for mode in modes:
        L, M = mode
        h_diff.data[:, sf.LM_index(L, M, h_diff.ell_min)] = (
            h_ring.data[:, sf.LM_index(L, M, h_ring.ell_min)]
            - h_QNM.data[:, sf.LM_index(L, M, h_QNM.ell_min)]
        )

    error = (
        0.5 * trapezoid(h_diff.norm(), h_diff.t) / trapezoid(h_ring.norm(), h_ring.t)
    )
    mismatch = waveform_mismatch(h_ring, h_QNM, modes=modes, t1=times[0], t2=times[1])

    return h_QNM, h_ring, error, mismatch, fixed_QNMs
