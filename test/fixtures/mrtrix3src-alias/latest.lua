-- -*- lua -*-
help([===[

----------------------------------
## mrtrix3src/latest ##
MRtrix3 provides a set of tools to perform various types of diffusion MRI analyses, from various forms of tractography through to next-generation group-level analyses. It is designed with consistency, performance, and stability in mind, and is freely available under an open-source license. This is the current version, built from source (not a release).


Example:
```
mrview
```

More documentation can be found here: https://mrtrix.readthedocs.io/en/latest/

Citation:
```
J.-D. Tournier, R. E. Smith, D. Raffelt, R. Tabbara, T. Dhollander, M. Pietsch, D. Christiaens, B. Jeurissen, C.-H. Yeh, and A. Connelly. MRtrix3: A fast, flexible and open software framework for medical image processing and visualisation. NeuroImage, 202 (2019), pp. 116–37.
```


To run container outside of this environment: ml mrtrix3src/latest

----------------------------------
]===])
whatis("mrtrix3src_latest_latest.simg")
-- neurodesk-exposed-commands
whatis("Commands: 5tt2gmwmi, 5tt2vis, 5ttcheck, 5ttedit, 5ttgen, acpcdetect, afdconnectivity, amp2response, amp2sh, blend, connectome2tck, connectomeedit, connectomestats, convert_bruker, dcmedit, dcminfo, dirflip, dirgen, dirmerge, dirorder, dirsplit, dirstat, dwi2adc, dwi2fod, dwi2mask, dwi2response, dwi2tensor, dwibiascorrect, dwicat, dwidenoise, dwiextract, dwifslpreproc, dwigradcheck, dwinormalise, dwishellmath, fixel2peaks, fixel2sh, fixel2tsf, fixel2voxel, fixelcfestats, fixelconnectivity, fixelconvert, fixelcorrespondence, fixelcrop, fixelfilter, fixelreorient, fod2dec, fod2fixel, for_each, gen_scheme, label2colour, label2mesh, labelconvert, labelsgmfix, labelstats, maskdump, maskfilter, mesh2voxel, meshconvert, meshfilter, mraverageheader, mrcalc, mrcat, mrcentroid, mrcheckerboardmask, mrclusterstats, mrcolour, mrconvert, mrdegibbs, mrdump, mredit, mrfilter, mrgrid, mrhistmatch, mrhistogram, mrinfo, mrmath, mrmetric, mrregister, mrstats, mrthreshold, mrtransform, mrtrix_cleanup, mrview, mtnormalise, notfound, peaks2amp, peaks2fixel, population_template, responsemean, sh2amp, sh2peaks, sh2power, sh2response, shbasis, shconv, shview, tck2connectome, tck2fixel, tckconvert, tckdfc, tckedit, tckgen, tckglobal, tckinfo, tckmap, tckresample, tcksample, tcksift, tcksift2, tckstats, tcktransform, tensor2metric, transformcalc, transformcompose, transformconvert, tsfdivide, tsfinfo, tsfmult, tsfsmooth, tsfthreshold, tsfvalidate, vectorstats, voxel2fixel, voxel2mesh, warp2metric, warpconvert, warpcorrect, warpinit, warpinvert")
prepend_path("PATH", "/cvmfs/neurodesk.ardc.edu.au/containers/mrtrix3src_latest_latest")
