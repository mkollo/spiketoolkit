import scipy.signal as ss
from joblib import Parallel, delayed
import spikeextractors as se
import itertools
import numpy as np


def detect_spikes(recording, channel_ids=None, detect_threshold=5, n_pad_ms=2, upsample=1, detect_sign=-1,
                  min_diff_samples=5, align=True, start_frame=None, end_frame=None, n_jobs=1, verbose=False):
    '''
    Detects spikes per channel.
    Parameters
    ----------
    recording: RecordingExtractor
        The recording extractor object
    channel_ids: list or None
        List of channels to perform detection. If None all channels are used
    detect_threshold: float
        Threshold in MAD to detect peaks
    n_pad_ms: float
        Time in ms to find absolute peak around detected peak
    upsample: int
        The detected waveforms are upsampled 'upsample' times (default=1)
    detect_sign: int
        Sign of the detection: -1 (negative), 1 (positive), 0 (both)
    min_diff_samples: int
        Minimum interval to skip consecutive spikes (default=5)
    align: bool
        If True, spike times are aligned on the peak
    start_frame: int
        Start frame for detection
    end_frame: int
        End frame end frame for detection
    n_jobs: int
        Number of jobs when parallel

    Returns
    -------
    sorting_detected: SortingExtractor
        The sorting extractor object with the detected spikes. Unit ids are the same as channel ids and units have the
        'channel' property to specify which channel they correspond to
    '''
    spike_times = []
    spike_amplitudes = []
    labels = []
    n_pad_samples = int(n_pad_ms * recording.get_sampling_frequency() / 1000)

    # compute spike rates
    if start_frame is None:
        start_frame = 0
    if end_frame is None:
        end_frame = recording.get_num_frames()

    if channel_ids is None:
        channel_ids = recording.get_channel_ids()
    else:
        assert np.all([ch in recording.get_channel_ids() for ch in channel_ids]), "Not all 'channel_ids' are in the" \
                                                                                  "recording."

    if not recording.check_if_dumpable():
        if n_jobs > 1:
            n_jobs = 0
            print("RecordingExtractor is not dumpable and can't be processedin parallel")
            rec_arg = recording
        else:
            rec_arg = recording
    else:
        rec_arg = recording.make_serialized_dict()

    if n_jobs > 1:
        output = Parallel(n_jobs=n_jobs)(delayed(_detect_and_align_peaks_single_channel)
                                         (rec_arg, ch, detect_threshold, detect_sign,
                                          n_pad_samples, upsample, min_diff_samples, align, start_frame, end_frame,
                                          verbose)
                                         for ch in channel_ids)
        for o in output:
            spike_times.append(o[0])
            spike_amplitudes.append(o[1])
            labels.append(o[2])
    else:
        for ch in channel_ids:
            peak_times, peak_val, label = _detect_and_align_peaks_single_channel(recording, ch, detect_threshold,
                                                                                 detect_sign, n_pad_samples, upsample,
                                                                                 min_diff_samples, align, start_frame,
                                                                                 end_frame, verbose)
            spike_times.append(peak_times)
            spike_amplitudes.append(peak_val)
            labels.append(label)

    # create sorting extractor
    sorting = se.NumpySortingExtractor()
    labels_flat = np.array(list(itertools.chain(*labels)))
    times_flat = np.array(list(itertools.chain(*spike_times)))
    sorting.set_times_labels(times=times_flat, labels=labels_flat)

    duration = (end_frame - start_frame) / recording.get_sampling_frequency()

    for i_u, u in enumerate(sorting.get_unit_ids()):
        sorting.set_unit_property(u, 'channel', u)
        sorting.set_unit_property(u, 'spike_amplitude', np.median(spike_amplitudes[i_u]))
        sorting.set_unit_property(u, 'spike_rate', len(sorting.get_unit_spike_train(u)) / duration)

    return sorting


def _detect_and_align_peaks_single_channel(rec_arg, channel, n_std, detect_sign, n_pad, upsample, min_diff_samples,
                                           align, start_frame, end_frame, verbose):
    if verbose:
        print(f'Detecting spikes on channel {channel}')
    if isinstance(rec_arg, dict):
        recording = se.load_extractor_from_dict(rec_arg)
    else:
        recording = rec_arg
    trace = np.squeeze(recording.get_traces(channel_ids=channel, start_frame=start_frame, end_frame=end_frame))
    if detect_sign == -1:
        thresh = -n_std * np.median(np.abs(trace) / 0.6745)
        idx_spikes = np.where(trace < thresh)[0]
    elif detect_sign == 1:
        thresh = n_std * np.median(np.abs(trace) / 0.6745)
        idx_spikes = np.where(trace > thresh)[0]
    else:
        thresh = n_std * np.median(np.abs(trace) / 0.6745)
        idx_spikes = np.where((trace > thresh) | (trace < -thresh))[0]
    intervals = np.diff(idx_spikes)
    sp_times = []
    sp_amplitudes = []

    for i_t, diff in enumerate(intervals):
        if diff > min_diff_samples or i_t == len(intervals) - 1:
            idx_spike = idx_spikes[i_t]

            if align:
                if idx_spike - n_pad > 0 and idx_spike + n_pad < len(trace):
                    spike = trace[idx_spike - n_pad:idx_spike + n_pad]
                    t_spike = np.arange(idx_spike - n_pad, idx_spike + n_pad)
                elif idx_spike - n_pad < 0:
                    spike = trace[:idx_spike + n_pad]
                    spike = np.pad(spike, (np.abs(idx_spike - n_pad), 0), 'constant')
                    t_spike = np.arange(idx_spike + n_pad)
                    t_spike = np.pad(t_spike, (np.abs(idx_spike - n_pad), 0), 'constant')
                elif idx_spike + n_pad > len(trace):
                    spike = trace[idx_spike - n_pad:]
                    spike = np.pad(spike, (0, idx_spike + n_pad - len(trace)), 'constant')
                    t_spike = np.arange(idx_spike - n_pad, len(trace))
                    t_spike = np.pad(t_spike, (0, idx_spike + n_pad - len(trace)), 'constant')

                if upsample > 1:
                    spike_up = ss.resample(spike, int(upsample * len(spike)))
                    t_spike_up = np.linspace(t_spike[0], t_spike[-1], num=len(spike_up))
                else:
                    spike_up = spike
                    t_spike_up = t_spike
                if detect_sign == -1:
                    peak_idx = np.argmin(spike_up)
                    peak_val = np.min(spike_up)
                elif detect_sign == 1:
                    peak_idx = np.argmax(spike_up)
                    peak_val = np.max(spike_up)
                else:
                    peak_idx = np.argmax(np.abs(spike_up))
                    peak_val = np.max(np.abs(spike_up))

                min_time_up = t_spike_up[peak_idx]
                sp_times.append(int(min_time_up))
                sp_amplitudes.append(peak_val)
            else:
                sp_times.append(idx_spike)
                sp_amplitudes.append(trace[idx_spike])

    labels = [channel] * len(sp_times)

    return sp_times, sp_amplitudes, labels
