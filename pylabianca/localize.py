import os
import os.path as op
import numpy as np
import matplotlib.pyplot as plt


def set_up_paths(onedrive_dir=None):
    try:
        import sarna
        onedrive_dir = sarna.proj.find_onedrive()
    except ImportError:
        pass

    anat_dir = op.join(onedrive_dir, 'RESEARCH', 'anat')
    subjects_dir = op.join(anat_dir, 'derivatives', 'freesurfer')

    paths = {'subjects_dir': subjects_dir, 'anat_dir': anat_dir,
             'onedrive_dir': onedrive_dir}
    return paths


def plot_overlay(image, compare, title='', thresh=None):
    """Define a helper function for comparing plots."""
    import nibabel as nib

    image = nib.orientations.apply_orientation(
        np.asarray(image.dataobj), nib.orientations.axcodes2ornt(
            nib.orientations.aff2axcodes(image.affine))).astype(np.float32)
    compare = nib.orientations.apply_orientation(
        np.asarray(compare.dataobj), nib.orientations.axcodes2ornt(
            nib.orientations.aff2axcodes(compare.affine))).astype(np.float32)

    if thresh is not None:
        compare[compare < np.quantile(compare, thresh)] = np.nan

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))

    if isinstance(title, str) and len(title) > 0:
        fig.suptitle(title)

    for i, ax in enumerate(axes):
        ax.imshow(np.take(image, [image.shape[i] // 2], axis=i).squeeze().T,
                  cmap='gray')
        ax.imshow(np.take(compare, [compare.shape[i] // 2],
                          axis=i).squeeze().T, cmap='gist_heat', alpha=0.5)
        ax.invert_yaxis()
        ax.axis('off')

    fig.tight_layout()
    return fig


def find_scans(subject, paths):
    """Find MRI and CT scans for given subjects in Labianca OneDrive BIDS
    structure.

    Parameters
    ----------
    subjects : str
        Subject name.
    paths : dict
        Dictionary of paths obtained via ``pylabianca.localize.set_up_paths``.

    Returns
    -------
    ct_dir, ct_file, mri_dir, mri_file
    """

    # we also need the path to the subject's CT scan:
    ct_dir = op.join(paths['anat_dir'], subject)
    ct_files = os.listdir(ct_dir)

    print('Found the following anatomy files:')
    print([f for f in ct_files if f.endswith('.nii')])

    ct_files = [f for f in ct_files
                if f.endswith('.nii') and '_ct' in f and 'preop' not in f]
    if len(ct_files) > 1:
        fname_lens = [len(f) for f in ct_files]
        longest = np.argmax(fname_lens)
        ct_fname = ct_files[longest]
    else:
        ct_fname = ct_files[0]
    print(f'Choosing {ct_fname} as the correct CT')
    ct_file = op.join(ct_dir, ct_fname)

    mri_dir = op.join(paths['subjects_dir'], subject, 'mri')
    mri_file = [f for f in os.listdir(mri_dir) if 'T1' in f][0]

    return ct_dir, ct_file, mri_dir, mri_file


def read_compute_ct_alignment(subject, paths, CT_orig, T1, plot=False):
    import mne
    import h5io
    import nibabel as nib

    # if pre-alignment was needed
    coreg_dir = op.join(paths['anat_dir'], 'derivatives', 'coreg', subject)
    ct_coreg_fname = op.join(coreg_dir, f'{subject}_ct_trans.h5')

    this_fname = f'{subject}_ct_aligned_manual.lta'
    pre_alg_fname = op.join(coreg_dir, this_fname)

    if op.exists(pre_alg_fname) and not op.exists(ct_coreg_fname):
        # read freeview pre-alignment transform
        manual_reg_affine_vox = mne.read_lta(pre_alg_fname)

        # convert from vox->vox to ras->ras
        manual_reg_affine = \
            CT_orig.affine @ np.linalg.inv(manual_reg_affine_vox) \
            @ np.linalg.inv(CT_orig.affine)

        with np.printoptions(suppress=True, precision=3):
            print('manual tranformation matrix:\n', manual_reg_affine)

        CT_aligned = mne.transforms.apply_volume_registration(
            CT_orig, T1, manual_reg_affine, cval='1%')


        # optimize further starting from pre-alignment
        reg_affine, _ = mne.transforms.compute_volume_registration(
            CT_orig, T1, pipeline=['rigid'],
            starting_affine=manual_reg_affine)

        # show transformation matrix after further optimization
        with np.printoptions(suppress=True, precision=3):
            print('tranformation matrix after further optimization:\n', reg_affine)

        # transform CT
        CT_aligned = mne.transforms.apply_volume_registration(
            CT_orig, T1, reg_affine, cval='1%')

        if plot:
            plot_overlay(T1, CT_aligned, 'Aligned CT - T1 preoptim',
                         thresh=0.95)

        # save
        h5io.write_hdf5(ct_coreg_fname, reg_affine, overwrite=True)

    # else/then - read the alignment or compute
    if op.exists(ct_coreg_fname):
        print('reading existing CT-MRI transform')
        reg_affine = h5io.read_hdf5(ct_coreg_fname)

    else:
        print('computing CT-MRI transform')
        reg_affine, _ = mne.transforms.compute_volume_registration(
            CT_orig, T1, pipeline='rigids')
        h5io.write_hdf5(ct_coreg_fname, reg_affine, overwrite=True)

    with np.printoptions(suppress=True, precision=3):
        print(reg_affine)

    return reg_affine


# TODO: separate function for reading channel labels from
#       positions unified
def read_create_channel_positions(subject, paths):
    import mne
    import pandas as pd

    fname_base = f'{subject}_channel_positions_ieeg_micro'
    coreg_dir = op.join(paths['anat_dir'], 'derivatives', 'coreg', subject)
    ch_info_dir = op.join(paths['onedrive_dir'], 'RESEARCH', 'additional info',
                          'channels description')

    info_fname = op.join(coreg_dir, fname_base + '.fif')
    info_fname2 = op.join(coreg_dir, fname_base + '_Karolina.fif')

    if op.exists(info_fname):
        print('Reading channel positions created by Mikołaj.')
        raw = mne.io.read_raw(info_fname)
        info = raw.info
    elif op.exists(info_fname2):
        print('Reading channel positions created by Karolina.')
        raw = mne.io.read_raw(info_fname2)
        info = raw.info
    else:
        # no saved positions found, creating new
        chan_info = pd.read_excel(
            op.join(ch_info_dir, 'All patients all channels_U10U11.xlsx'),
            sheet_name=subject.replace('sub-', ''))

        ch_names = list()

        for row_ix in chan_info.index:
            current_row = chan_info.loc[row_ix]

            if (isinstance(current_row.electrode, str)
                and 'macro' in current_row.electrode):

                prefix = current_row.electrode.split('-')[0]
                n_channels = int(current_row['channel end']
                                 - current_row['channel start']) + 1
                area = current_row.area
                ch_name = f'{prefix}_{area}_'

                if prefix == 'BF':
                    ch_names.append(ch_name + 'micro')

                for ch_idx in range(n_channels):
                    ch_names.append(ch_name + f'{ch_idx + 1:01d}')

        info = mne.create_info(ch_names, sfreq=32_000, ch_types='seeg')
        print('No channel positions for this subject, you have to do '
              'some clicking!')

    return info


# select only specific regions micro channels from info
# regions -> str or list
def find_channels(info, regions=None, micro=True, side='both'):
    assert side.lower() in ['both', 'l', 'r']
    one_side = not side == 'both'
    regions = [regions] if isinstance(regions, str) else regions

    names = list()
    indices = list()
    for ch_idx, ch_name in enumerate(info.ch_names):
        ch_type, region_name, contact_type = ch_name.split('_')

        if regions is not None:
            is_region = any([region in region_name for region in regions])
        else:
            is_region = True

        if is_region and one_side:
            is_region = side.upper() in region_name
        if is_region and micro:
            is_region = 'micro' in contact_type

        if is_region:
            names.append(ch_name)
            indices.append(ch_idx)

    indices = np.array(indices)
    names = np.array(names)

    return names, indices


# TODO: make sure if mne.pick_info operates inplace or not
def pick_info(info, regions=None, micro=True, side='both'):
    import mne

    _, idx = find_channels(info, regions=regions, micro=micro, side=side)
    info_sel = mne.pick_info(info.copy(), sel=idx)
    return info_sel


def project_channel_positions_to_voxels(T1, info, stat_map=True,
                                        stat_map_sd=3., channels='micro'):
    import mne
    import borsar
    from scipy.stats import norm

    T_orig = T1.header.get_vox2ras_tkr()

    ch_pos = borsar.channels.get_ch_pos(info)
    ch_names = np.asarray(info.ch_names)

    # take only non-nan positions
    good_pos = ~ (np.isnan(ch_pos).any(axis=1))
    ch_pos = ch_pos[good_pos]
    ch_names = ch_names[good_pos]

    if channels == 'micro':
        # take only micro channels
        micro_ch_mask = np.array(['micro' in ch for ch in ch_names])
        ch_names = ch_names[micro_ch_mask]
        ch_pos = ch_pos[micro_ch_mask]

    # find channel positions in voxels
    ch_pos_mm = ch_pos * 1000  # meters → millimeters
    pos_vox = mne.transforms.apply_trans(np.linalg.inv(T_orig), ch_pos_mm)

    if stat_map:
        coords = np.indices(T1.shape)
        dist = np.sqrt(np.sum(
            (coords[None, :] - pos_vox[:, :, None, None, None]) ** 2,
            axis=1)
        )

        dist_gauss = norm.pdf(dist, loc=0, scale=stat_map_sd)
        dist_gauss /= dist_gauss.max(axis=(1, 2, 3), keepdims=True)
        all_distgauss = dist_gauss.sum(axis=0)

        return all_distgauss
    else:
        return ch_names, pos_vox


def autolabel_channels(montage, subject, paths):
    '''Autolabel channel positions with DKT atlas labels.
    The passed montage has to be in MRI space.'''

    # in: montage, subject, subjects_dir=None, aseg="aparc+aseg", dist=2
    from collections import OrderedDict
    from tqdm import tqdm

    from mne.channels import DigMontage
    from mne._freesurfer import read_freesurfer_lut, _get_aseg
    from mne.utils import _validate_type
    from mne.transforms import apply_trans
    from mne.surface import _voxel_neighbors, _VOXELS_MAX

    subjects_dir = paths['subjects_dir']
    _validate_type(montage, DigMontage, "montage")
    distances = np.arange(0.5, 5.5, step=0.5)

    aseg = 'aparc.DKTatlas+aseg'
    aseg, aseg_data = _get_aseg(aseg, subject, subjects_dir)

    # read freesurfer lookup table
    lut, fs_colors = read_freesurfer_lut()
    label_lut = {v: k for k, v in lut.items()}

    # assert that all the values in the aseg are in the labels
    assert all([idx in label_lut for idx in np.unique(aseg_data)])

    # get transform to surface RAS for distance units instead of voxels
    vox2ras_tkr = aseg.header.get_vox2ras_tkr()

    ch_dict = montage.get_positions()
    if ch_dict["coord_frame"] != "mri":
        raise RuntimeError(
            "Coordinate frame not supported, expected "
            '"mri", got ' + str(ch_dict["coord_frame"])
        )
    ch_coords = np.array(list(ch_dict["ch_pos"].values()))

    # convert to freesurfer voxel space
    ch_coords = apply_trans(
        np.linalg.inv(aseg.header.get_vox2ras_tkr()), ch_coords * 1000
    )

    # try various distances
    labels_dist = OrderedDict()
    found_labels = OrderedDict()

    for dist in tqdm(distances):
        for ch_name, ch_coord in zip(montage.ch_names, ch_coords):
            if ch_name not in found_labels:
                found_labels[ch_name] = list()
            if ch_name not in labels_dist:
                labels_dist[ch_name] = list()

            else:
                voxels = _voxel_neighbors(
                    ch_coord,
                    aseg_data,
                    dist=dist,
                    vox2ras_tkr=vox2ras_tkr,
                    voxels_max=_VOXELS_MAX,
                )
                label_idxs = set([aseg_data[tuple(voxel)].astype(int)
                                  for voxel in voxels])
                current_labels = [label_lut[idx] for idx in label_idxs]

                for lab in current_labels:
                    if lab not in found_labels[ch_name]:
                        found_labels[ch_name].append(lab)
                        labels_dist[ch_name].append((lab, dist))

    # now rename from DKT atlas names, to human-readable
    new_names = dict()
    for ch_name in labels_dist.keys():
        new_names[ch_name] = list()
        for (region, distance) in labels_dist[ch_name]:
            region = rename_region(region)
            if region is not None:
                new_names[ch_name].append((region, distance))

    return new_names


def parse_part(name):
    parts = ['superior', 'middle', 'inferior',
             'anterior', 'posterior', 'caudal', 'rostral',
             'lateral', 'medial', 'isthmus', 'transverse']

    for part in parts:
        if name.startswith(part):
            part_len = len(part)
            return part.capitalize(), name[part_len:]
    return name, None


def iterative_parsing(name):
    rest = name
    new_name = list()
    while rest is not None:
        part, rest = parse_part(rest)
        new_name.append(part.capitalize())
    return '-'.join(new_name)


def rename_region(region):
    ignore = ['White-Matter', 'Unknown', 'Vent']
    translate_hemi = {'lh': 'Left', 'rh': 'Right'}

    if isinstance(region, np.str_):
        region = str(region)
    for ign in ignore:
        if ign in region:
            return None
    if region.startswith('ctx'):
        region_parts = region.split('-')
        hemi = translate_hemi[region_parts[1]]
        rest = region_parts[2]
        name = hemi + '_' + iterative_parsing(rest)
        return name
    else:
        return region