#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analogue Grain for GIMP 3.2. Code: GPL-3.0-or-later.

Install: ~/.config/GIMP/3.2/plug-ins/analogue-grain/analogue-grain.py
On Linux/macOS mark the installed file executable (chmod +x analogue-grain.py).
Requires NumPy in GIMP's Python; no profile downloads or companion files.

Copyright (C) 2026 Zarir Madon. This program is free software: you can
redistribute it and/or modify it under the GNU General Public License,
version 3 or (at your option) any later version. It is provided without
warranty. Licence text: https://www.gnu.org/licenses/gpl-3.0.html

Renderer: bounded parallel tiles, cached tonal responses and optional SciPy
FFT acceleration (BSD-3-Clause); NumPy remains the only required package.
Noise generation uses float32 and a 16,385-entry tonal response table.
Spectrum-method research: Ian Stephenson and Arthur Saunders, TPCG 2007,
https://doi.org/10.2312/LocalChapterEvents/TPCG/TPCG07/069-072 .
Film-frame scaling uses the image long edge and the
measured scans' 6.35 micrometre reference sampling. Output pixels retains
the previous size convention. Pixel-aperture smoothing is a fast Gaussian
approximation, not a particle simulation. A cached expectation LUT reduces
brightness drift from the density transform and clipping; it does not
guarantee identical means on every image or colour. Modelled choices add
a restrained fine texture band; this is not measured emulsion-layer data.

Measured B&W scan statistics adapted from grainifier (MIT code, CC BY 4.0 data):
https://github.com/grainifier/grainifier
Data licence: https://creativecommons.org/licenses/by/4.0/
Profiles: Ilford HP5+ EI1600 (single/mixed), Kodak Double-X 5222
(single/mixed/push+2), scanned Nikon Coolscan V ED at 4000 dpi.
Attribution: grainifier contributors, 2026. Research inspiration: Newson et al.
(IPOL 2017) and Zhang et al. (ACM TOG 2023). The colour-retaining use of
silver-film measurements is an artistic treatment, not a colour-film profile.
Modified by Zarir Madon for this plug-in: reduced tone-binned density
sigma/skew and 32x32 NPS-derived kernels, with a separate zone blend.

Five Fujifilm sensitometry response proxies are adapted from spektrafilm
profiles by Andrea Volpato, https://github.com/andreavolpato/spektrafilm,
CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/ . Modified
for this plug-in by sampling each published density curve's mean absolute
slope at 17 exposure positions and normalizing it. These source profiles
contain no measured grain kernel or stock-specific grain particle parameters.
Our procedural grain textures for them are therefore interpretations, not
reproductions of the spektrafilm rendering pipeline. Source revision 3bb2c2d.

Single-point manufacturer RMS figures for Fuji, Filmotec/ORWO and Foma
provide relative-strength guidance only. They do not specify a spatial
grain texture or a full tonal response. Remaining artistic interpretations
are explicitly unmeasured. All 30 options render with the same grain engine.
Sources: https://www.fujifilm.com/nz/en/consumer/support/films/negative-and-reversal
https://filmotec.de/aufnahmefilme/
https://www.foma.cz/en/catalogue-bw-film-324

Engine revision 4 adds artist-designed fine/coarse mixtures, restrained
clustering and luminance-balanced colour fluctuations to modelled choices.
These additions are interpretations, not new stock measurements or a full
spectral dye simulation. Colour grain, Clumping and Fine/coarse balance
are active controls. Colour strength is linear from 0 to 100; 50 is half maximum. Clumping uses cached
variance fields and relative moment-based mean compensation. The five measured B&W choices retain revision 3
behaviour. Existing saved recipes retain revision 3 until a grain is selected
again or Reset is used; new runs use revision 4. No additional external code
or data is used by this revision.
"""
import base64
import json
import math
import os
import sys
import traceback
import zlib
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, CancelledError
import gi
gi.require_version('Gimp','3.0')
gi.require_version('Gegl','0.4')
gi.require_version('Gtk','3.0')
gi.require_version('GimpUi','3.0')
gi.require_version('GdkPixbuf','2.0')
gi.require_version('Pango','1.0')
from gi.repository import Gimp,Gegl,GimpUi,Gtk,Gdk,GLib,GObject,GdkPixbuf,Pango
try:
 import numpy as np
 NUMPY_ERROR=None
except Exception as exc:
 np=None
 NUMPY_ERROR=str(exc)

NAME='Analogue Grain'
PROC='plug-in-analogue-grain'
PARASITE='analogue-grain:recipe-v2'
FORMAT="R'G'B'A float"
LABELS=('Natural Fine', 'Ilford HP5 Plus · EI 1600 · single scan', 'Kodak Double-X 5222 · EI 250 · single scan', 'Ilford HP5 Plus · EI 1600 · mixed scans', 'Kodak Double-X 5222 · EI 250 · mixed scans', 'Kodak Double-X 5222 · EI 1000 · push +2', 'Ilford Pan F Plus 50', 'Ilford FP4 Plus 125', 'Ilford Delta 3200', 'Kodak Tri-X 400', 'Kodak T-Max 100', 'Kodak Gold 200', 'Kodak Portra 400', 'Kodak Ektar 100', 'Kodak Ektachrome E100', 'Fujifilm Velvia 50', 'Kodak Vision3 500T', 'CineStill 800T', 'Fujifilm C200', 'Fujifilm Superia X-Tra 400', 'Fujifilm Pro 400H', 'Fujifilm Provia 100F', 'Fujifilm Velvia 100', 'ORWO UN54', 'Wolfen NP100', 'Wolfen P400', 'Fomapan 100 Classic', 'Fomapan 200 Creative', 'Fomapan 400 Action', 'Fomapan R100')
KEYS=('hp5-plus-ei1600-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'hp5-plus-ei1600', 'bwxx', 'bwxx-push2', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass', 'hp5-plus-ei1600-1pass', 'bwxx-1pass', 'bwxx-1pass')
PROFILE_SPECS=({'name': 'Natural Fine', 'kind': 'Artistic', 'rms': None, 'scale': 0.67, 'gain': 0.72, 'tone': (0.84, 1, 0.77)}, {'name': 'Ilford HP5 Plus · EI 1600 · single scan', 'kind': 'Measured', 'rms': None, 'scale': 1, 'gain': 1, 'tone': (1, 1, 1)}, {'name': 'Kodak Double-X 5222 · EI 250 · single scan', 'kind': 'Measured', 'rms': None, 'scale': 1, 'gain': 1, 'tone': (1, 1, 1)}, {'name': 'Ilford HP5 Plus · EI 1600 · mixed scans', 'kind': 'Measured', 'rms': None, 'scale': 1, 'gain': 1, 'tone': (1, 1, 1)}, {'name': 'Kodak Double-X 5222 · EI 250 · mixed scans', 'kind': 'Measured', 'rms': None, 'scale': 1, 'gain': 1, 'tone': (1, 1, 1)}, {'name': 'Kodak Double-X 5222 · EI 1000 · push +2', 'kind': 'Measured', 'rms': None, 'scale': 1, 'gain': 1, 'tone': (1, 1, 1)}, {'name': 'Ilford Pan F Plus 50', 'kind': 'Artistic', 'rms': None, 'scale': 0.68, 'gain': 0.61, 'tone': (0.75, 1, 0.62)}, {'name': 'Ilford FP4 Plus 125', 'kind': 'Artistic', 'rms': None, 'scale': 0.82, 'gain': 0.78, 'tone': (0.89, 1, 0.74)}, {'name': 'Ilford Delta 3200', 'kind': 'Artistic', 'rms': None, 'scale': 1.56, 'gain': 1.28, 'tone': (1.3, 1, 0.67)}, {'name': 'Kodak Tri-X 400', 'kind': 'Artistic', 'rms': None, 'scale': 1.18, 'gain': 1.1, 'tone': (1.16, 1, 0.72)}, {'name': 'Kodak T-Max 100', 'kind': 'Artistic', 'rms': None, 'scale': 0.62, 'gain': 0.65, 'tone': (0.78, 1, 0.7)}, {'name': 'Kodak Gold 200', 'kind': 'Artistic', 'rms': None, 'scale': 1.03, 'gain': 0.9, 'tone': (0.96, 1, 0.76)}, {'name': 'Kodak Portra 400', 'kind': 'Artistic', 'rms': None, 'scale': 0.9, 'gain': 0.81, 'tone': (0.92, 1, 0.79)}, {'name': 'Kodak Ektar 100', 'kind': 'Artistic', 'rms': None, 'scale': 0.59, 'gain': 0.62, 'tone': (0.78, 1, 0.72)}, {'name': 'Kodak Ektachrome E100', 'kind': 'Artistic', 'rms': None, 'scale': 0.62, 'gain': 0.64, 'tone': (0.8, 1, 0.7)}, {'name': 'Fujifilm Velvia 50', 'kind': 'RMS', 'rms': 9, 'scale': 0.6, 'gain': 0.65, 'tone': (0.82, 1, 0.7)}, {'name': 'Kodak Vision3 500T', 'kind': 'Artistic', 'rms': None, 'scale': 0.91, 'gain': 0.84, 'tone': (1.0, 1, 0.69)}, {'name': 'CineStill 800T', 'kind': 'Artistic', 'rms': None, 'scale': 1.14, 'gain': 1.08, 'tone': (1.1, 1, 0.73)}, {'name': 'Fujifilm C200', 'kind': 'Sensitometry', 'rms': None, 'scale': 0.92, 'gain': 0.85, 'tone': (1, 1, 1)}, {'name': 'Fujifilm Superia X-Tra 400', 'kind': 'Sensitometry', 'rms': None, 'scale': 1.12, 'gain': 0.99, 'tone': (1, 1, 1)}, {'name': 'Fujifilm Pro 400H', 'kind': 'Sensitometry', 'rms': None, 'scale': 0.9, 'gain': 0.85, 'tone': (1, 1, 1)}, {'name': 'Fujifilm Provia 100F', 'kind': 'Sensitometry', 'rms': 8, 'scale': 0.65, 'gain': 0.68, 'tone': (1, 1, 1)}, {'name': 'Fujifilm Velvia 100', 'kind': 'Sensitometry', 'rms': 8, 'scale': 0.64, 'gain': 0.69, 'tone': (1, 1, 1)}, {'name': 'ORWO UN54', 'kind': 'RMS', 'rms': '≤12', 'scale': 0.96, 'gain': 0.91, 'tone': (0.94, 1, 0.74)}, {'name': 'Wolfen NP100', 'kind': 'RMS', 'rms': '<12', 'scale': 0.99, 'gain': 0.92, 'tone': (0.96, 1, 0.75)}, {'name': 'Wolfen P400', 'kind': 'RMS', 'rms': 18, 'scale': 1.36, 'gain': 1.15, 'tone': (1.19, 1, 0.71)}, {'name': 'Fomapan 100 Classic', 'kind': 'RMS', 'rms': 13.5, 'scale': 1.04, 'gain': 0.94, 'tone': (0.99, 1, 0.78)}, {'name': 'Fomapan 200 Creative', 'kind': 'RMS', 'rms': 14, 'scale': 1.12, 'gain': 0.99, 'tone': (1.02, 1, 0.78)}, {'name': 'Fomapan 400 Action', 'kind': 'RMS', 'rms': 17.5, 'scale': 1.36, 'gain': 1.15, 'tone': (1.17, 1, 0.74)}, {'name': 'Fomapan R100', 'kind': 'RMS', 'rms': 13, 'scale': 0.98, 'gain': 0.91, 'tone': (0.94, 1, 0.75)})
SENSITOMETRY_CURVES={18: [0.3, 0.3, 0.351, 0.507, 0.672, 0.915, 1.107, 1.204, 1.32, 1.337, 1.336, 1.312, 1.297, 1.332, 1.363, 1.326, 1.249], 19: [0.3, 0.3, 0.3, 0.421, 0.599, 0.869, 1.087, 1.213, 1.349, 1.361, 1.359, 1.324, 1.302, 1.357, 1.412, 1.425, 1.404], 20: [0.3, 0.3, 0.369, 0.638, 0.924, 1.235, 1.383, 1.318, 1.272, 1.188, 1.173, 1.183, 1.196, 1.239, 1.258, 1.223, 1.147], 21: [0.3, 0.3, 0.5, 0.984, 1.587, 2.0, 2.0, 2.0, 1.757, 1.594, 1.46, 1.205, 0.808, 0.403, 0.3, 0.3, 0.3], 22: [0.3, 0.3, 0.3, 0.54, 1.317, 2.0, 2.0, 2.0, 2.0, 1.671, 1.414, 1.071, 0.625, 0.3, 0.3, 0.3, 0.3]}
DATA="c-rlq+0HFVZk^wyuB!_%5B(~FE@ZoH36hP5+JXcKdUunNJGs97XZEe#4XX+`*eA~(GGkznYb}!h{>$J0vH$Lm|MV|^`rRM?A&-=Pm;d;8fBMrezyJ4N{@X9V|C{t%>c6Jna!+6X6}wjaAFBV+{EzN`jJCi2vrE&jukX)iyR`eS*}pv7m)jr3|ET^)^FO-(G5n9|e=PsQK2iJ=<>&1*yHxyo{r>r3mzrPi{>ONJX!`Z_<3oS@>o0%$hyU?E|L%JFF-spg&0R(vYx<q1X^%PD+O>^TW}SX*l{)>?bB?~!I`7-LkG1TbEAL%uJJ;;*d*wDu+oj~?gPTfUeO0?;|1{e^*V@>*rJPH%n>~Y@?Q3h@eayA@O#XHD&w2D#M{f4LXEJhL_I=x~+5PTT>UFKJu~V-pjXX=auB}=|>8-7q>YV4=-o4hA=PGH}YwAlY-R`X0KP`Lo>)IIA-jrKktG98UqRchyK~qa}_VcT?)Lru!t(4X5<$2dm_V<yu{i}WBIM=HEz-n#S|Glp3+Njg6R-1s?@_E*p>@zkK+3zNOeze-O+Knt5#8t=mOS<L@=^y{_ueOeCp;y}ic~4tMHVbD0MyffFy=)}<Z0Ay&xfNR)%SLz4T6|7wYSsTfr;V2_RUdyFPkW=yf&EOguk>GEsgG{MQnD?vde)H5?D=T!HQP9TmYS`ETx_}8im5&&Y3!Xxv1u>149eM@$)>yKIc&^rJ=iimYuDDqZuYCi9;S5rjo;ZtTd4ldQEU<Iopw7{OP}@)qnypP+Ptp%hi$pGJZ!IY+nRoH+UmCVtulABU!3Qn4@$M6oIWzu?(=uPI&D$+m4^+RU9ubXi>-HCWGnk3ox7j!w54goQL+uE?b+JSciK)d^OkHy4I8Chu8ZA=sIQW35A{~gwK<c0-o|9+ZmaDt>6)#nfBeHg|F1v%ldY=1Im2%YVb)(iFWoX<FKg}b>V9FXCEnQMpLc8b)qcO#CiniLG4lP=hVt_TC9QeCv<3QE2{xGJe(5XZdT9e*KHqEhCXdHeyqDjA@71s29(ZTCUp)k$49}}k(8$DK`3U~)ulLBB6~5nL%lUq(pRFtD7iRoIytSoqU+i`k+%J1f*x2_onl&Efe%x}cSbJ$Z@AC`A9_)U@j9V|ZA@e=BdEMh|rX}8M+wUISI`5Ze_U~`VpPgj)u;UGRrlG;j@$7rWQ;rqmIO~d+JJy)}<$l?Ej8j=N2G~!i_+X2#%TqHHLuFfEKi%S+=ZdjR@eM=cuD8ye?zh@Til?{xyvJnSyT``5uY3H;z8B}V9e7`z<9>sgqZuEIu`V&HKArdQnDL6+t>qpQw}>%lS^HKCb(9$|hgPk2wpL^w7~Y<>XyawcP3u0sVrCcz*n#`j&83h@h?%wX>wd#5qs3#!ipuv)*d~r8S6Zy~R%3yiqi~ODYcUn(jl=|%n6}i`{S)pw#VyPoOF(x%W6^e)4BI331CJg1Uvm}DSm)3#FBo1urk#B8vDIW+V>`7hqtNg5V#6C-Z?qf(S$n)}v0P$L*x4DIw1oXJ=j;C2U3#qUaSxN9@3%QMXCT&Qilwvl4CbS4eQ#`_9wRnutc7)N1Y6PCfVRuyr3~ND5X1_UF|`}i*v>8+kYYe**c@9ST{a-5x|Z1ZGwTuJ1dD&3Si>vp5wPClNa>nr^C03IW=zx8eXX9g8nb3Emf#pQ)~GpadP9jhk9)vR%z{NO8{Oi@ldMVDxz^$b%rIvdhM|?av@n~#qr{NxSff)1*V204DOSM3U0Qe)izQ7xajbnB@utHyj9S|o$*hq~T01*dVcYSwGuCV0W9T7b&{q7eg;QD>F(RvmeOzhBv%@IrDO)eGOxMQ2Hg)!y)r!@rHCMC7B$nqK@pamEYifAIMlUVN)$~<6R&vicGGdRFid&Co8y0}X#}XXFttAz2STadn-olXW57S`-630M_^>1;|(p2E^h`Y(Lp3H@h{a$K}Zg1L}6}G)=muic}Iboq|+@sm+xZR|yF%P?Ip~r8{2zl(r;G%@{V;+PSFIG;B$Y}BxtClS6CWf@fd22(y<e|lw_82`2d$cfO37L<Q<!5I7#MVfMX`WT)VYYC!Y^lfBwK!pBI4NUksCSI7y)_0^Utq3m*Qjfi#DZEH2RIukeypc3hZe(zE6{cF=?+^w+R`FgBko}98MZu2i(hq%OKYHM5oz^Kt&A#Tm&%sii<cJJ#_;ZNW9&!5BG}OsKQd#X&7|3Cad;h&<etOWrFL30xm|I>&p7sre6gxAn{h_0dsh^5b!S9m5o@$17h>H<fjPS2>6RwXW6$`p77oiE1$(f*4U|j*{JO5CH8Pe~kwYJ0o88-eKl%QcJ#ZLFIV|?OVbez(x^0CuY-7m>2<OKPatr?|;)GU53|Y?+5r<(cb}Zj-Sn2)=3$5<+*COKhm`(dVkanKk9~&h`x@m$+jmXnHxLA@i#%Xlzr8sKj)fIU(IvV$i0MbT3{4)F9xZY+s=H|pHcI{e^v2A*V9S^^)>27iyaywiEcUgNxVKLCV<_L4c3A7=-w}cJpeG86p-B)YOrTLSALKL~eJN9BRZ!Kmm$2jf{KQVT>8MW|2YFPR%w`%SkrmEc5!*&>=#umn*So{`?Abh%FZbnQ;n>vu<km-zDmco@LD;fr&<v?3<EHk^`c8|%r&5ytAifzlU^S3qs@VkQxA2jD)+^L2UwwbuMQf(#1c^5>{@ts9xD8<^dhh2)RVw~lcnKjrl%`0$>7`YaQda;wF--By@Xat+PMkt?_%${t1gDX`dU@nNXWAWBlL%BrUYIZ6HG#qQY#rxT876Ogw4xn7GT0e9`I%m#j{{_@$zSZZYW3~4#uyuRSm;Gj}Dx7LCWAOZk5G%Olg0xl~!e(Q37@=NbAEg;iLp3)y?johIbS>;ui^ZQ~t=ZuvH?>Vodzj#HUn~w3-``sAC2S_*p2+5V=kwBDxBI1=Xs5Ak3)PE9uyxQ6pxte$-n+?MsC$968H=&m(R4p<70{!&d@HydFl{Bgu(lNA$Kj{dXUhm<6(eW{CPudHdtsZa*~0eCIG1~b*k;S)oHJLY%5}J}GB9rnWn+O_EZwv_uUF%uxO~limaK2rcGw9IDVDPhcC4ngDz1IKk)KYow6YNTVcZUH5_hi0(L>_VF(4SGGkCAdbp)`gDZs>NwlIU3R!laQfEI>U3ziTT7M9z!2(>c5SL?Ncv8*49U0VPf1KS0gNIQ$$4Vw^KGxjSsF}C*E@GnO87>I}2fH8rIq2c+0J7<5IE*UhKIT$~fL>S7;e9ESNo7pZ))dSClk%sApL5G=#@yC%sY*vQ{juag<IDU92ZAE7E4*Xzy9(OtfaWwM4#4(D)6-TZPVjRynv~iT{e8-`GxdQSJ!exLP@zc40%K=A(?unchxH9l>;Pk*1l7j@diO!U}Sg*cqze7<vYI5J;)X~+00|>Ve&Z1mMIF@ub<%GgDrNat07S63)UOK{XkL5Jem6n4Ix0}v5Ty!|@^5*5@!|`W0;6cJ$U?w_Ekp|-rL?scaFnZ}PagL%N#6*mg5I-TRQmlmt3~?EvHO6p=>=5rE3RG-}h>>w5qD#b_h(r;eM$n2#*2CL3P+`aLGekz<%y1e(HbQR&<OtIhyd$J%ppWn$pa7r(00@8;1U3kP04xEp;tRqEU_@XCArJr~gi8dX09pay0vN`@nO97#1^7qgg20cU4S+;~jsPeDW&-#`NJ>Bz;46SyfV#FuN1&LXG6HCT)d02$fg>yjunrL37c$tMARwSYJpw^Q`+`?Uch>v_C<$8%%ml;<;1h5tK~h1d08|090(=D|3!oP8EkIpBy#Rm#3j-_$giK%=aI+W*0@JZA0Ad5$)&v0rcWC^bkf=|19iTg)cmVW(?Fr@!;ur1@GJr!W;sA64Nd{35AS6IjfV=>u0ipx+2S^c8B_L45st5`b{Q^=3R1F9mr*&TWw6*7x90FwoA_?>oNheTIBB*dSg3JQN1>#F=7$`CjXQ0zSvXOcdAqScc<Q*tI5PhKkND7K7oK(I<`~<QQ6r>ZAuXON?K?A)B(i2oD2vV@7nhMaXB3(hnlAr}`D>4@pFNj~z!T1SK#~_qJGlP5vB|X0=f+wXdo*U#hC~*+wpwFFDe-$!RQL&=t#n<=or1eSmgUTY#US1>w+INr}NQ3}s0tE|{F~|T)@<1U3<Pi`}NJ$|S2a*d2F(A)?XaiCX2t2-IAA7xNa9lj{l$5}tgt3Jn1u_+iSD+`5&;{}riDDp?p@0Um8i;Ku!I3Zravh0yAnk$P2O=OygCH1!j0oZ)NRA*xqC5$rrIGWif^6x7in*ncf^Z7>f<#u3UO|urnU=&`kaSV#1^E|5VUUVZKqgrk#AcA7Ntgz?8bxd{%#gcD1P5sx1ap+pL0kvP-4q)^1!U7Mr<{*6sr@DhgiKK4K}ZTAG?e@hqC`p+Az+lO5n@M3AR&yTToNKmNGm0{gbb77Oh`5%<b=GFnoq&%gtVk{az5>EfBof;|M0*6>)-$K`{7v+zx?-q{=5JAhd=%Ddz<zD`1P0n@~{8)Z~x!tVc4bJzmqY{5Lr}_Jh#CP)^f58lAY(-A_DWLJ%XD1WMkB9e{n&1vLmS-tk1RS&E@%fn`c(83=NC+x(CvBoY_Hp6!OQ_wQ2T4!ED^Q)_prwGxrC2xRNm}3^a$Q>~f^+*JthIw}^0Mcc}Z(;OAsMm?w8w0BKI;=cBgBwyWVC`KkHJ?I^D5_w<l?B8sDTZ{3mcW)I?^`IVvJcFN3z#e!qiE2pAtk9oG=P~TT3hwav6w{LFlvIjr6VllVfVDZ0}S+D#Fvm)lsXS>bqZrGJTVn@4qC?_#wd_HTjf*osSrtL}6=gB-1z>zY4+;$DhU)wdMXw=`1BWe$A#?t20cb3iW>YmeLvo*%K)ND8{zA<yKZ5Ymmv%z=6Zqs3(o$KU+SZHgL?%52>vsbS4ozZPU+31-??AN#1us8E!Y%x|J?Q5NyLziqGQ*G7m^=w;P@os@iwbg3wM?P!ZQ490pO0_Aieq3MnWmP|7Z8P=bf?c-C%%%NrE4$cHGH<)z?3W$Z_BG)6#`O)JXfV?>7sLkYc9GWhc;9x9KUTiZ-0XBmnzrP(S+{+eTYF(<-p1Ef?(HKwylmUXHfry-DKDexp{_qsInMp{#$w}T4so?XOBP~WfJ$lhc*Oy4-}@gx>#z3WO9Dp)6Vt=vEa6%Tcr~x>K8DN_yFcWHDgcN8Mr)T3Dd%a*$+T$--g^XK+iQ@%ZUk>MH1NrjCiA$8_wpO`z4$fW1MUp>i>KhT>4`ZG`gi%_Hj3fG6vbr<5{gjcqOdpEwJcyHi-2+xLcV5%T2Ps`B~fhX9V|Er_Dl*N&)vE`m3=!-c2r5*5-wR8;7g_BFb^?U-y%tPUD9$2m8h}Ag8Z=I2v@KOXe%+3**T`*1>+4Ba+O@O0`{Z`NWg~f4B(grbg_7s<ddz*-B*0n#Q=6;v!#mn4X1HIkQC_|U`fnT4$5W9YcKM?19RTc1cpmJ(*|N+#hHrYlR8<CU=J2!E<nCCt-+!<*fA2Ln-$$;dDo?3ULyonv|jdxhbk?bUTQD)E`VrCIXa}Clss_P4e-4O(jPwgNU^;Az=@C%n7;5iq2;mHdQ6q=DrqSOQYRp77f>-Hm?#3`ih_UNjqy@4Vg!eZ1#FjVpd;q5Nzh6XOolLB=(C-4ffSWk8RjC&=oRs<+N}W5lLVq5S|oK|D+Y3kNj9f#i+>iDsxUkWE1C*u1TaW;ky1=b6F4-rOtt*Wo>K-xn4LMSvB|4Gm}O6AQoyd!X#)BemF*Gtju=$)l_B61z8w~2%A$)qDWJd+?Fpl3E6h`g4R0S(x>Y=}yJ<0waR3Q*6s9nfbU;&VpZJodEm1{dRYl;oEyYJA+3_68h2wn1cT54%1A(ky%qh50iwL<cOL})<s<8N3iy*~Fo8^oL%?;@h@pMHvWfDJ-g-;h9HmzujRS;GJLbvk!q?+EK60(p`9A_G*Qe-E(PPQCLsW}U6t4btopll@!?E=On%!U|LXYwdORiz<qIwhwDC*%()o|l7L;VVuJsO(>?=LPvK5)i@K$&(OCtcU7F_@2x~)>xzgBuR!LRlv<c;ue%g%GO<4q=0aRQGxlAhQ;C*0l&%$%}5TDm{ccn905)6RY&fi0I<OE+=5Ik9|6Nv%$l-=sAa5Dv@7X{pt@H39HPr&qxPg<lQhF3K#i_PsPXQm5J1}qGz4<sifon)l_*NlCtcc$Aqu#XY0=>%{-jA^6bPI~>0lJMp_Cv*tmJ6Si4fX6pJ3fkJT)q4<@GWGW#inDpQ+KZCySvS;_Bs-jHdL)m;F41$pb^wS{u@X%urI;npMigmY^hbJ5zkfj)ZxM;&u?#?$QR%9PX7l&jDrUq$~;%k3%Q#6f2d(kLnv>x%@XB(s}9&bS{GI+D$Rq(n7S|A_iQPvPkPuA!i7?CV5!v%m_z1ViXe?p;7_!u_8jMPB-20*ey`AC%gZ7huqSR=?IHk$@+YV*_O0)p@y)pmj*!ARBoqEn9&e!$mv0b=>*XjF1vY+p}8s|EwUn=q&@C+L*&88+d=dahhLU<X-j_+x=8=Az~{r)w?Vv}+?GR3;c(ae(#-iSVC}(BIn*2Pjg{H#*6-2p;nI~I)S5XLS-E{mKq;t#A(%kPklN1+>8v5<O0g`93k+j;dslu>DAheWl|VviqoUdq;$I!;Lx-WPsg_Wks-qEZK$G~R?GV7)YLh+hm(<n6uMzsY+tk<?+`rY$i@PPswcZB5iZw14U_?}msv&ti9k&9)bg4#wyxASj{lV`;tyho>13heP1uv4MR2J<YK?=^T-Tvi>Qz7Y~E*tFn)DFGB*NkIi66}!U*<!j)xx<*#d5#^)Fnxv6$baec(t-5PecR;T)7zaDxJ^hb?SgPe4x2^z0Rglnz#s-UNyF135>HFHCE6wC`^CyNw>SjwX@yP{dip9SY9S*>y4D=>T!tW3G4Dz;uMz9D6i?iDx~=LozxBR4+u^m64d^_n1=(@$i`cP&Z!10%as9L)^`52_w6a|+QuYTYycBda`6@1{6G$V}L|SznAuS<epan>p1l`nt0&7*~T3|SCH^LTgc$+d28c};_fe$!Udn}<awKeRZ9XFxWpEvnv&zMerfd_H<a9eA1i?K4U;f+5&&M(DJ;RbM9xK}&?9!n34A>hDhxO@!X7H$rG59R^0gE`|&Vtz5}SPNPqST|TrSYudu*Lu_<{a(1&V#W%8tb6SS>=Eo7>?iIr>^<yA>{IPt>}l-uv;W_WuG@>J6_IU$#i1RNae~Ey9n->;#e*G$b)?OdCB5vcjBeOr*yf)W8+IJl9X1~)0nZ1V8aPXE!sy(=X~Z+iPp(&YVasL7`*|wzti%b5bCocO&fqwhaa!XH$H|WK9$vuZ8~kt>a(I{UEa-2@3xRI}kA?b0IXCcl;0M7wf<crg1^>#Ou2)yLORA25LJ~=MdGP(<5z0;M$kq=VBQF!aCp=R4sq|jq)6&zG{|m2JzA`*$_|@>X;e*pNhrdoIezd2*uP6Yq!HF2&f;5jXo^c1F5yU8nToBLn{)~GR4Jkxo+!RBD2#awSqA|p1h}<HYQ-p`OkI|rFL`05^ClO^f)Tc=68Kok2MMUdy?kli>N*#!n5ko7oM!bz6T#-5Abwu%q?RCls1t2y6!~i$~&;>ybz#)K62*26%quLYk01g6l1bSiMC_+~RxeU}$*{@)j&PL)pKz)D(0U>(Bl87(~cLEwEh>ExsH1@){gnWJBVdaUN0Zk*uhFB4J98fyw<I4-iC*%)!;1@>LfhWGAxG0DuR1#Pv5lrElKs$kf0vQEf3KSLCDiBxTus~;l*#gN0zDv~ii46lW296AL8JIJWXu#9JtV?>qw}E;)7XHRQ6y<RPQ3uWr^c|Qykb2_x^$GL?{RbxirU2onNChwrq94Rbkf+G~$!LJ*_(DsOq@Xku@Fzlf^-$nn5QmDXasK9&INQo95DNru2#gVUB(O{5oaDfOpXw+HAbpD*H?Usd!oZM$H{;-nV-xd6J`OA$xH~X<-#lM?a)MwA!5@NE1lQ=p{>uBx;i_M?R9`qxFrnZ_!J2|g9WR_Km{{<$U~LPu8|g)v4lWoBF?nN+G&tst>R$7WqXu&==N>FKxNk7x;K`jme-kY@+y27AV-bk22aEs3?YE~0K*|8<1*jr`wxEO9R7QKK6TUPH*h|tkK=nW^1QZg|O+ZZnjRlk!tm7$_2DBRzbU@|d+K*SoP2@E}KLS+=v?frXK$k+b3N$QIwm|QK#58RQ>17IP)JRzay$w`2(&j+1Lmdy)J<$9>2?Tu*RYTGeL1D!Dohv^TN*<cLsl`ekZ|Wy%qM(!#TMJ<rv{q7JL6-%!mNZ;Yc2VyoYDCH~>cs}QGil2tI7_`flxftfLB;kh4683XI6&FZ%t^c;bzP-L2Bl+FJOJe$^n6tLLHh?qpx9+;2camG&JgNDY7(JTl#zw15n4wmAgPOlT2dNHC@ZPAgbEYdOei*~;}lX=-&CN`hW7gBGc}55YW!%P@t^4$OxG~4-{S9GhwaIh4kGHMf7<lPFoU$x>2P2kjLXH%L0I<wt0#d)0iKfKf9C3m^L}l)pxo=)*y7w**9|)f>{yujsM{~}b=kJG?QU1S<iWT;X{SV5_HEvt;$%J~v$NR|S8trL($(0*^BwGDaXq@X_BfZeC(srD`_9*WY_A2Acbj(26WXt9_7i!p>G8AI$G8$sJi)|s9=-Lz^;y^8yJc~OyytTHwbnf%b<O{FGkjh1RE{ep>a<U|zOUFG_slT6rE|sGfmr<LEh3ty`TMo$i2=>N+h2aZ%`*k;!K)3}Y@hdeA&_>GUF@0Hb<LgMW2WsBt6pPr@>a|Sc%XUJ&-uT5NjVy}`s>L;xbm|-in9&W=E>44!*=etcGvXtv@iRD?q!AA$;enX|8qMuyFIzbx2GpLR2wDpQg-hj#D2N2>XjzkNS-W>>j3vMJD&e=bx*NXw7mn0jhQW~bY*T>AmSOY%i@ad&Y)d$XLnl!=apvKMZUNr=q+OW0G}3}-_e8{%5H1TX0n}zC|Bx+SF@@fCyY;@kdwMmhxhB)p3k)U$KZLoi3g4&pT-&2=e^U1J-0U+u^YHf#Ow_)Y?U85|0n);U~qc$-IHgPU+){<^W_AX9?oB$u5BB$-Cn@MX6j~OY_z-;j4eDH$NUMsifw&Qcj<Nu>4U$Umtb?#Y;?K<O_w)S?btu<Q@p{4&B*wFd+{Yl1C;@a;yeW>DC^RfM86QSscO-f2|`6>J_p0%J2QBfr0P{FC>RO+UK}nc6iF^r41-tkUVQ_;m%pZaz@71a@f3JAJn`2!)chPXA4x%=kUuw-Yf1VJBn}G?=o2sqfG%|hNKFOqqZDrGN>`EN7nQ`)R@u;~a#}~Ak=5e<V3Pv8_mt`ywa9AJNh>N76nQZdxTwluZ%DZngn<oVNqR+7TAJcv3z9U30_Kr5KU*>Eek%krSmYmYxPau<pF^9$lB@b#peGidig%nSYZaB3T7U+2_109>97VLus>GZL7cWgbdtRu_ucA8w?UXr<SQcf+w_NxcYo8UDRkvoTkb{b_D6V7I3{-JRf{g@;EjjJG$+?cmC0AI8px7evFFmMgcl5)z&QWD81!`;_`%-nn_*7A~*MO33O-cGxMB-|rFM$u{g#>al0ho3KoLZs~>27NFG`fH#lu9-vYsEg9is+~CA7MH>bR#7Nmr&ABq3o#eQAA;v9F-a@xIto@BFCWg?3nJ)>Z_$h`Oc!>J+8&0Zf!8v=5vaVOG?=Tf0R@ktmOPs^Akx5s*B$H7M9diU!kLOiYgw@tUCEBZ?%*L1lBOP11HzixJfzoDzPChXe%o7TxAvB(o{v<oir0VG=;-$)uY;&)RHu$Dh8NTHCWT1Re2<!<*n)|EE<);3+RNhAVXt|0zwHioDz3rJgcUil^oTfX`yhQ9-^Sax+<ZlVpB`#C?hi^A-4*LNE-$7kD4m-c~(`~maf{X5I;uovy!z-%m~HJZb4PSxZ3oIFdeA$iW3h&()bcW>4WN-p<-ZxzuH{KVkl8{!V7q4S8CtKIVV@ilUP-@6NTh9r25E|5HY?{%|~8L8jq4ht18`YX&Y(Cd`pE9ioJn)htgE|o($AP6*(5~5!65i)h{P)6%`a!4KW=rN#}A7GR*V1<J3}7I#jaGvYJ`htyv7}M*TU|n^X%g0cyL1<;Mg~6)amQ*Q^TQITKz&%5scv6u>pZ9Tq8X$kx=MPRYqzWo=X<*K54VYuGfQswo4j6>x8=>bckB1j_+z6@mD6YbqpD!-IebU8*sAxUEN4ySXmmMfkv_Nww62gz?Ryy6I>Jc)uDd<BwZWtB9l#4SiZ}7+jUS?5Z-Vc5O*DgEk<1yDz|zU5aOoSZO$L)e>VO<zlGXruyVHo#Rub=5IS*6d#yR6~DF=0L;MET_hDXYr^tX-PlS)-4SC2Ol5<Ps7L9zgz`GN48@ef6ly4GT&hwcRBTfwGOH+nx386k4XOS~HxV1aog?XZRGqb>>a&JA#S<mfK@L@3QSrT+LVoEbi0_xW1*oK4{}Y)T5t>v7)$aF(UZmRD8M9K<A7JPn6eCs@YtHKHv|(R(SZL>NIgw?m^t*&MN=x=4Fe(fje#)r&98!YHl95AAOPC$i%&xID<=&oF#Xk#%R~4SEJ2Zqd{Nv`JPE^H?GUh4dT~%^@8}#_vFs)Su>!*5u7R5?%<=L?<Oe`d7QncEvquO{V+R>M*W=W;9D50f^=v|&+R}Ea$k5%EdCOzEL<j8S{2(8CjXrcVE9RrcC@00RdTk5V6aD*=jNyzU9Yf0tKcNC`fqR^82hpgZ2b{y!+$y2%Np_0HWtHNGsTA^{G%OH6Td-W*@`l;4f6g6v3aL5*PFB2EML;w#o;L^IS!MyFD<z0P#3n@z!YilKNmE@Idu43=y^p^JdN~bTjSIY^?-7DurasHx|rTG2o_-KG;I{d_B&Iw@|^0^d5O`6olVy$5fYh!PX8Sshkp-3!H+^~ig;3M)KDxuw{iX$o;sD!+@CMCh_dLI?u{QI79Z+w1fuN?ut_?Az(DrZza49v;v{WK?tJ_TVgn>}10l~GLDzDAgXDn_axC5otyyj1B>u$mgB#Hmrkr92JMV@P+<z*Pm3fsmFhzseR<eSzyx9de+$qzW%ac7m5=ie3qkhhXudUtPbAhMqd}Xxr=IYSVAa*~qI8_~**~9(|5q_Twk;!?+#XBySxLq{qa=V@L=k`|!Q4)Eb9`CP6c!*}@cZVp%g6u2<+(wOp`>u(Zy4(;DQu<WjvB@4cR}$e&9;HUzc_HV?KGHyXB|T7uNa#>I9%oBYMB3Q(1y;87==0!st)1DhmE1^Wf723rRU2)lS$OPSCgX0_a>#btee+GJR2*l$>M*m|4*JQr|U=nTQhg7Zd)&^ejjyn&>{3H~+?JspKOAbD)!Fx3%@gBQm!4rv_SIM8v-Ux&XOg76XOIQ(!Y<YU0`fI9*w1+EJn8n`)fe&i9+O_K9;T&j0BmffTJ(_NDj2iJ}cAKbt_9_}KXNO_j<HsONGF{QgoCswYl9A0|Ca*f$xC#m$5gvfHa;fKRHUqBVYk*9kPr{6<?w-Bs_bw)UWAVZ-C0+O#VWeR7E6Pkez!XE`i2$dK>A*@2M#Slz^8NxLLaR}`c@F5Ija2OFI!pN`SvMW$hIwQhU1g(AxZx90fGzP>%mD#640X4#G1>XqC5vU`4M^KMY9|lRVfUtru03l!nfDK;|gn$Xa6#y{+Z2<5P4Dt<*Ky)YoQYZ#M7l&eA@UH{iJOG_9m<Qm`D^LXZ2%r+6CgD%Oq*8leR{*pCb2X;yjqwD^0G|O=Bh)4U4zL`+Izo5}4=%zAARwSY0EQ^91Mqfkg&u+;1yBOEB$x?^6Tm0oP=Ta?P6em}W+nIvNESdX;ah>aLV5xJ3KRxZ`~@HbRtDHi2wJC^;Oc}9O*-Z$3h$5o$~~ZWk)U({?1J3^#uK6ka8Ed2Ab)~6Lu>7dRG|7oD2Qet`GAHLAOS>QfYi_gK`H`L1XKwL6wxXoTR_2(m_ah;M3Eg3XHNM%i6GEJB8@;Lku3r3^hHMPCvgQjizFAQFA!p&$;6$3Py@>*>J5Zkuym}>4?>SLAILvZf+7k*AA(aP&u(vjrF{D;PZE=qFWQc)lbFa+Ql|NZS_R=M8W!a27iC+tQT7YrTU3yeFyMM4l2v(9k`NHlU-WdH4=P*yHc9U<%2%Ce%iOPOg)ZJw<U1&N5cQz%LF$X@hXCM977*YM1O$*3Kx?qk7&rFAmsnxVr?3HX2Z$gbjeuZ+G72dPB)I?@1#t!>8()GaWls$>+39F^)ZUaM(35~>m-YmT6zEi-UXi8+N*C&1po)Q3MgkhCYQD5Kp}?Uo2WlN?cnW4!6&K1=7(}%YG(=DqL2pFu9JNVlziY%4-#%qf5J!D!#RM!$UZtpRrl&ZIk}U|iB=3Ugi&C&Jfmnnd5|cq<M&TLcXxhsXu2IegksG9M(8EzHra_XR&SiG5(nifB#d>KxWqlC)K>|p@(^rkS^^g`qZy2jmvP6m%Az`GjQF2F!ASI22U{VpHe)MZ-CBlR>6M{|1I4SOgcrKyn>l%MHE5o9uPydYnOvU)>oKb9PdA$|)7u-p6xwtFey}dyl<+QdtnG(w##&LQ@jZcz=*)@wEY%)rl^@2-%?&}GDy<x?r+Hc+AOt0`?KV8ht_ixivF1p9~SKETMYVr&TPeWUmz}~UM?mYs$ZB9z_N(wQXVLxMb*rJB@0c$-3o1TO>{dPV#H%_wxU(orJvtfax#q-UMrPbD@xnHNoMR%tsHSfuEZrK&=-srhG6nJ$6>GqJ`48fel{K)~?-UgxDD40_`=La}me)Imk-n03`gm}x_?ppcn38L4vZXfmfMjkh>PntzJO*qWuu-M;QPyQ8NQ&xs8w(3vT+U^#}w$o&N*dnl*vU9est0PJCy!z^ec<jA(AE3CL&yD3pot7iQo?S4;te)O(CzaPxI(v6{nrAEXv=Gf#BNUsTZiBoI)?PmuLbk4K)i$3h_u+l>JHC91gDh{OawgA{IP7Qb_%S0^=GpxxMeI1p%Zs5z)bII4+nX_Z($Dyu<c=pTZ<^s$%$+msw1NG4EuPe0$LB!o=RNk-_QrMF+;yU5TW7-wh>!QRZP)pZDl@0Lt~0?C#G4~L-YLW4w|S*%T=a(xRr8WE*E_vgrhSKJ9M%3Q%;XvF-gLxi5pyZiU-!kAyo{zg7iy#BeV8LtTe{&MosqqX6I$iz<yP+m^{QF|CF)j{9MRIMO*qwmU8$@VpWV?@6Ig|-crU(z->YB4J@C$Ozjz8f8=e^BfYITY_09%%s!%cdvz1*d`Zk5VU7FXX8VMajaMT<S&)1;rRZJ3Pj7~CL&>7(ywm{12j&%GQ#hUD#Qs3vT;;&Tzk$SDot{S(>OGbHF6o$%5dr_sOuHtfCje>hqYWhYxe^W!)rsP`uTtSIjH6v7oik-xbDMeq2v7;}veO6D4D&4dAa|Pio68F8_k5V&s_~S{%S0#&>(RS6op`w{a_^k48HDszCWW%evGbmx9>d<Jxis5ZjT^8-uwRk%^2&$C79PSo?Y*x>|hT0e^`ZK|aDaZS(X|uu#wO`)QW_PucZ_40MHj5y_QPhiG>{-jo&yp}PDV?kjRGZc4p?RICR8`^VQ`Msi%tS|tG_6OZ^DDhw@JkJ3JfmB#3LW>Nnoq){m*`dV@PvF!^#&}Wd(|c$v8W!hpa#Dr4G;{gDwPTd?JXs4D#ED;TCl5eDC#pQ5eqpK3%xJJzbnbSCnVKt?V2~hVhLLdHb2`7wt`wqs$Z-!GUjEdmiAI2OxCd?=t+e;Rf%9I^~83E3S+2t0LnioX#!0(1&;VRAhEe76Fto#SI?-R9K*g)?s^CU(itbJq2n}DoNUK~N_|v~j+tJSd{U}}y22B2(k_SIL%q(Iiq~wNt_wZOQ49;UsAEm55|tWTdh5b`zl?I8Ns<DunYq+R7Lc?OEF=PMqLT&?e>J677Riii7pz%hmN&*uZAJhmHK7}Ap;xUK{WMXa8gf+-R{dGG(V{oi9TytkpPkglP>C2-B11p19MhY2RD(=AhSVMQ7PXnW3LtC4FO9AgsV8W;@#T0W^Yq=#Reb=kDp1x!N1UB}f|LsKU#gPpw3p<6E#=`Zwe4Oq^z#drmY7NSB64#yE3>N}ljIp~0oIahzE%As>VriYu8x)=NP!g$KcuNcW}eDLCP0o21yq#~T+yisD82G_mX5V4nbcBd^HQ;-wM2BHdu^*wfvbhP1k9>{#NuW}4%${STgYTc1FI9R<3s@&EA<2jB+8_SiR)3^#>-NU$M~YG`&KEijVey6t}oqC*RM)+y3m*f4V%HT$yfJ6l;znBeC_el;Cod(LqdlLRKC*&lgk^^Q_!G6)uZgBig+w4;#AeisY!snym@m3rTL((jiKEyJ5pn}fSc=66IpyJWVHsCT~qd83~rGn^v0e+CMr2kCyKj1szM-AR;Tz7*DzJo3PP+c&6Bz{tHZlTwIM?b0?7+O+;Z&{M^f`W9FkR)Gl!x#>R^_JVoq5tLez9&$oHKnlZciTO*ORzH}9>Q?9$|T)o%%}igY1R*H8HM1w6Hqm*b3-H-mQWI1DW~RFwd&%?PqBstV=1ZPq1R;THeZSo6(2guBLYL<y}231C}dN-h^BXAR}@M0hz{ft#v2xPnx~nJ5mLl4eLZR9D1~Uc5z;L17;{Tx=CpwH-uxNHuP+YF;kJu`U+KERxRzXVI07)uqP_;IDya+97@PrplKbQ}skkkdR(AD7tCH*tHhNb#0R7sgk$O%&N-brK{<JxV|B0XNS6{DApb_XtrhIP^%?Qz?z~)YZ*gbY36t-7F<;#E2}7FmOIpy;9;|~qv&~ztwcXw80W6>-lKA_`BPUfZkMf&t3FOSWdW~+t(9-j^Z8P9e{NgcLv&QPl&VwZJeT~$qWaS*RLNefPE`<f@=G}1A~002?<L#2B-(L0RaJ9zkX9PM7`vruuPURR)_7}4u;R#(y0`jzzGc)sin)*Qj7CzqaBn4WmEGE|qjN0X8<e-}nuJI-#Ts_R=pNiSzFLiXZ#7B(u+b4WHldgOk7rTm!7R5}<hsJ=nj-|N=Ibi?#Z~2@(~WUPvd4(pz%I*E{ZcfR-QnrWMOKY|bLgVUgPnhB{&e55S4>nNvCpjfDot18j4|gKX6NKTC8$2v@Wvk>=a=HAa09q4+$<kK4~ECZAYf=PU}qTL)^5|;t;xU?Vd5~IoMcQr7DTKPEFCN;tuidVYe8z2{#dyis?cx(vGB1Sut~6Wu%WQcxcRUpu~D^sv8l1uj{$hIx_P5p)$TuS3k(j;5R4N|mJFH98;l-Iq3k1!=4DD{Uw_zK71+Iu^G}lvLk{x}qmQEi2Lz7|940tobnxIf!Xb5zt~YmKMUhJ$fN?6~sN?~PW0i-kj$jqCjakP5j$@sNy<C9HG5B^Cj{oq}srYg=!U2I>0%rv-3>+J}J8**F8p&aT8%5{JU9MM0cc%Pw+~m%|$%E$ycThee9LEViSIr8;@q{}HCzY-%99p=!aDL?y!%>F&45u2dHXLxe<#5)&T=@BL_7MUwFz|5UElxv=Dg_+~MHrwUY+*2i5C?&edZZ{wV(6rR3Skz4FN9=Yfm(!b2<i~(Apk^Jh+q*BB*IDtn+ib@m?~UF5X;ck1KwAZK}JA9GeT(u*a*83j3Y#6;Er%!Fox!`D-%FqLxT?<wipmY9#1fW5QTsXqD=rf2z>w$5ljN`gpdk=mh=kBbO_*qkIjrK@)F1@lIW<J^n{QAED>%J&?aCCa1}r-KwAL31cLz_BV-03P4F5(Hz09@=K$IX$^*a$*pFbKAVL6#gcB3+F(A8Lbr6A6qLgAq|BUhkKmm(>fu#lH3me+@CkQKO7I<xafpvlP0szKV7g$UP8Nf2&W`NMB<R-wHF!p-sHb@}c;qg~iqQ?XQWC!#PKprr?z<WUY#QA4?7O5an0Qx|r0;mQO5TYeOR*=E~u>raRBnX+2`lL`utYB6|!jPKLVF((>$(>h5E$t^iB!mG}5(p;HP9mc~QGvJuokfxh)Rzb`&}1afM5%#j1N#O_4x(F;ccAo0^ojlxCkUbt^dU$^P>moUotAv1gVibn<R&Oj5TOt?i!ud~3VM~KtEgBIw4iN4=7Qn{@e4W_Br&LC5XzvL#XWD7W};oqFO)WjZqVN##X*&WKzCaGMa<CDas}Kx8GG^gVE4)SLkXb$4%Gm(1X37$=?=CW7-$qETtKEAzy;8PA{-@>fO0~5iZ7MLkY-zo4CpkV-hie<LJz7x#_VrGoF@Cz-oz%9pg@=cxe7!qX}$<vB!iJQhJqQ$XdteE<OV_<sdEz2`h?+q$$Q3|6i5Og$c7*$g2V{IBdC!oIwz(9In%3(Oi5c1MNx?c0TpCb5?eun1z{HCS`u*;02zXyAp(;$41zJp$RIAG<P1VI$<rWOqm&H-H^|-~hJ!?o!a0p}dFvu?8|X~}*^o&iNgsz7@;`_IWx*gIMCU+CHd1-W5-C=k06|082(=?5kP=2hE=dukq?Hg{GG2f<Q;JQ<I4SN-<ooA~JXE4lkJbKcV~ssODICB1<G=jr@5_HCa(wmFsE&Tx`LG?aTj|ocd!JNuZo3yyX`k#4^MTE?U1oH~xJrnatJUXpv~m<rTsYTkTAP~_-x2w0zOiin?Jwoz5r0}pERTJ6H%HbE`40?h?ql)J2-TAb@5U9jJ{?=F4t};PrDA%w5BJB+eOp&HfkhK`l9*H4yBEAT9Tm(j&$a9HS_0+d2Y8o-;pvO^e|Jdm2>%_v@+a{AJXh(NY+u-`Hx2Q=0(XLd$AK2VSny=VY4j^;pxDq4582(^pX7pkFc@#i(7P8|xqc&iMraymAWjG8?5T6p8#{Y<kaoSz{n)i^1iUHrx^8*RQ~bi_#P;mEZqG4!2l?#3)~{v!mvW8mHSBiHJbAmlblfxBGTBqQrsuxd(42kN9eXpk(PlsIE2*cJ)jfhZEvZgsr0q%XChoaD4$Iek7Nz~&bAi0suN}whbjUCZVc%H1Y{&2}-dAIjVN1)CEbP<!vjV$!z}Q|{!xN3p;&m`@xQ}M%lqVzwKbb8g=p#F$(C*Om{7lV`TUn6xIkme3o!y&sEI(;Jw#t^Ef7ZSf7kBn@A=9S6dD6<2ow0o@cfW1Bm+{HY*uDll_uTfuCyiqs%>8Um@3~R0{047wVm`IE^Q-ytTm3HU2^qX=%g0sn1S~&{o9zLMd(N$!1JJx9h<7V-jP!h^x3g)sypyj~`$YQ-y!evEvDJmoB2uBns;e9zns=8|cUhI%QGuNt#M23oe;vP-p^8b~S?4}U`vyxLC2I8j0-n>{<0{bRAob?OpKsOY@um2w>jr+@R94nO25*Kc0D4pn0EY#G<wGd2PHF8z6JkxDN(EU+DQY7Npk_Zn0Tv|)jkLf=Mq#sfSuIqIYJcGeIWrc#{7DpEzb&+Jx1%UBcP(&=%4mVMRo0=8KJ#X3TK!B^34Lc46qCE^`4-hhmKzlKws+F*&n9Z-$}K>lDyyyFxbK2`cB)1o9z|L-h3X~Z5AVIHChr2yRf?_#ZPQdGp?aI4U}~hujG{EDfnf;5uBu^LE5SRM(;$pA(&^KT@l$byq!Jivr~FllKmhepvO-lyJJHJ3jD%490pS`Ho|0ItBu#V7<~iwZ{yUVTWpA|TYmXurUK%C}TGbF!MFzUFOu?O~gp@tJ8po=tZ3Hk_{oY13WCmInezdB^VxX-^h%O@QqDWnl;zt$OlZs`IA>DTUYG@oRR8r4x5$6sFt!I1B6>Y3Nu{F4!(ByR$%o6XgR7*O#mU`i^stRmDHbqzSsy4T!sXyY35+x;4{)Fh;-39-eqn?JHj1#r%RP|nELqt{$6^YA=WtZmE-Hc}wYg<5C+o(d`R97ki#mI^oCHC!T5YnY;i4mXZxv<rhRDIk_fL`9YHuNh&i&oWRJQ=vE2pR|}!Je4jc!rjOPV}W43eO54MZ(evyedF}q~e)E_D(kS%>tvs&wjcC#T&9}?F!GHsuWq5vQ5TR+lR2W7CESdXJl7)oTy>7lCB=`A|Mep<jTs1TM@Lk8LKNfobw_5?UY^SP2+$=k=hi_M-YIlpoRXZSy(HItZ$_^#+jL<6&HlEhKMcIm+&rfDj$YCxhb!tLZdJ3aYN}km3Un`mv6IRZv00I;fL4#BD|0^0<oL%V6`+z=RcorpIrM>bMbP?ygBIjwSKt|UY_t#RJq6i+ma|c6<c_80;O%#g$$}fYE;v*6|XknQw73{+T1ji6tufW8)$(&DxjB?d#T;Lv$PRkuEy;+nvZfXvHBSy)Fz3ktVt=bJM=9c#a%UAy_@={SZ<yv|9aP^y#zr?l9p2RQpBbgh3fJ#?upJ}Uy7QEcU2J`68Y|6qKYa;XO$4njfH;X-<CwlJ361B!W#NBNgE48D@uGRJ!J+}>qm8PMymDKqin6mc~NRHbQx}EuYvT>e>B5uebmFwoVPH(Az`hOstr}>J=m;83^U3^d&LX;o%vfwF<6hTLul7kDP0RIY8H8=Fu>_58MLNEDw^I}<0abq_Sz)6inA*Emdk1(izW*%%<4P5ixhfGx$^3fG11_8e73y{sOqn`m#w6&rE;{<uwV^&3gHczVT>xpt;Y75DqS3iLQz>+7T&B<`s6KucdNC#Ohgt@THWD_n^b}P7CXace5i7%8YD!3=3tD986gQq^%ILq#Ky19Ai&#{XlklIbE_x;i$vX~8u(GZyacysQCTS9cd%S$eI!o{Jz1J$k7Jq6Iiavq5d{_gR@>&LV0h%DJOOEjheD;?)aFMj>@LMiODILDMmJlrR98tnG<8y_QzLM$JDk@Zozra@-Cmy^Kv7G3R299w&73XN+p4(D{k(NZQ)PB^g(_<+l|ZVHrE+98{9&tuC$^gn_1~<_C=#cM1mRF#W+f9i%O1gFX9Jg|UhokS%0&#xMRk3y23je9p@A>pA;UJzbRWJ$=b4>mw@voGw@UN78ZJAKnPh2a2sZn@6YLb4@dZ&;uZ^k#ZYWNzT5+*MB_nkQLwuuRu?8}?s4r+h8LO)^K~u+@ve2CfrLd%u*ijBR>c*N;mW$GE17`laEyw)Z=6R7q`eugVUcwgl->B9~AuT-L^bx(D1_g&A`y6Y%MNK$|D?T5w!~1q0y{>WChZRPZU_kiHs2PB+bZ9b`o{cvODu5JOeRo>b@L`}2ZW09eQbo~=4~6fjCiO`CcE73dK+Y0bx+kV0*!K*2ajVZ8W>5Vi3$A;88jm}5<7oJ{f4+v=J3C)KnVE<ChDVNt;DxZlg2$kSn{@V@@UJCcA*=eOBE2dPW~o1GWMuR))zD|Cp$?7~(T+KQGy7sSA}tXCqAv7|hO~CtU!}D^Zr0}nRjq-@obXy={T|CY)hO#DYOgL@Ikdz~SGcgQcz~Dgx8butzY)K4{km@Hhc|fL`_CuhbL#0aCK#zRes7C)M_|QC(9~eUIE|WIOgR^VmWQ&}w2ZL0t|g~+h^5KZdavMnRb#3D+yK}X*JjZUicN)$_H+AjQ);VzZQ#I*eXBn$Yg0H2djTs#+aiktJ4EXwlU3)#vccZL3c@yC7E@;Q+q7QhRwnqTU52%W4Zke=x3#}cfS>0AP756&I9G7m;0!t^)0;Q2B8>va8xB1jg*YI2Y$7K^M=TCrIhs14actu-#}R)W{6D;b%SHI<IMAJtlL6NQ4hh{9mGqWFLpKL{Q@KR)li)lZm+H-vO_GNvY3G<Z-|EA$qq`?35UwE|M!Au2F5z;*5rumSrxl*8+*>%kU#_rHUK&$&+QY$y+YM(NE;<}{y7O}K;rgF|;3X0(QUoceK&XKL1Yrq+6@)MdY#8n+2tsItfC*s~f-8hv2*ePcA!tJ=hX4;@AA&)IhzJ}JPBO?u=!t+7VXA`H6Vkq*2qy%72F41P5kw=jM!>Bo{0hx~#_KIu288wq_yGd|9Jn9@;RwPO066@FAqY+gvH*}lcmtq^ppeqa6EYEw0&oS8ivuw)(ANQOo^Xyp9zZ_;goF}(prgQ(fH94)6S-s{0kNe7TU}t55UwAn*FnMnkNpJA2&D;NqoV~dZX%mP%ouv+0OSMo2SD%(ChYSStd&%n5M)&~iXx~@Af}*A0G<~N`V$*%1BJ&@e}bzAaup8yjm-+Ly)aw=yFcMxfWQ|t9B^>}$N-gpLd^i62}=X4PN?prT!DpZhu{{rw7-DN1*8LBC+IFH9soUHdw}_X_yPQb4LBh|&<0`>zS)I~aR3eRgPQ<hanTs!HiGT|1@e;^0b3#p1;h&JmM;<}l{YeM#|9(8BiaXK5Gf+$-3FN?hKaP3$S6=$@}`5yy6CN-yu^Zm8v99@kwz1_2FeXYTzVz#K;@C(6YU2w5ELPaL#GqpSlpby1U3(<vat8k%x`3;1hOJVL6;Wf1O~ARx)mfWs96xUpmD|Rg775`EOHo>F^FW+%OagYMU$WwZOx)iXor#|)tTrKCk4VG&!3dKh<4ELAmvHbi@*o153(N;00;vh7l4QWxq>P%3<-jS36Lv5!~kgn1P_owP#Y0~38*NftdQISB8)F-#zuiK@i$P<9NEWH3?hjLgd>!bKx886iS#HGselNsR0_<8^aX+#$Ydy<k)#Gf8^~`U%7Iiz`W<$*k<<qQAj*Ou7LtSr!Xn6xjtJ>Z`c-w@LCGX}(?S+XNTDE*f^14!D(b5wT9XtD0xin6D(V9T#z5g$LyxjJ+#wyKpiD9|h|efVlTc0SHA>nbbd&rIqBu(BTtN3CTXenMQ-ud@o)mk~@j=}u$={^|{3#CXFq)wwgt`!#LnskH`otfi#gfsDJkm6zdgS7lLK3=3s41bbgz^%4OsF)W-K3%uI!{-B{@Ij^I{)K8{tuR<Bkl"
_PROFILE_CACHE=None
KERNEL_CACHE=OrderedDict()
KERNEL_LOCK=threading.RLock()
BIAS_CACHE=OrderedDict()
RESPONSE_CACHE=OrderedDict()
MODEL_LOCK=threading.RLock()
FFT_API=None
FFT_BACKEND=None
TILE_W,TILE_H=768,384
FILM_FORMATS=('Output pixels (previous look)','35 mm · 36 mm frame','Half-frame · 24 mm frame','Medium format · 56 mm frame','Large format · 120 mm frame')
FILM_WIDTHS=(0,36,24,56,120)
SCAN_PITCH_UM=6.35
# Artist-designed texture parameters, not additional measured film data.
# Fine-band weight and local variance modulation for the modelled choices.
TEXTURE_MODELS={0:(.22,.06),6:(.30,.05),7:(.20,.11),8:(.09,.23),9:(.12,.20),
 10:(.34,.05),11:(.19,.16),12:(.25,.10),13:(.35,.04),14:(.30,.06),
 15:(.32,.05),16:(.18,.14),17:(.12,.21),18:(.19,.15),19:(.13,.20),
 20:(.25,.09),21:(.31,.06),22:(.30,.07),23:(.17,.15),24:(.21,.12),
 25:(.10,.22),26:(.19,.13),27:(.17,.15),28:(.11,.21),29:(.25,.08)}
# Chromatic dye-cloud strength, R/B balance and spatial scale. Artistic models.
# Values intentionally expose visible chroma at maximum; Colour Grain is linear.
COLOUR_MODELS={11:(.18,1.05,.95,1.0),12:(.12,.95,1.0,.85),
 13:(.08,1.0,.95,.75),14:(.10,1.0,1.0,.8),15:(.09,.95,1.05,.8),
 16:(.17,1.0,1.10,1.0),17:(.22,1.05,1.10,1.1),18:(.18,1.0,1.05,1.0),
 19:(.23,.95,1.10,1.1),20:(.12,.95,1.05,.9),21:(.09,1.0,1.0,.8),
 22:(.10,.95,1.05,.85)}
def _colour_gain(value):
 return max(0.0,min(100.0,float(value)))/50.0


DEFAULT=dict(selection=0,amount=35,size=1.0,softness=8,
 shadow_selection=-1,shadow_amount=48,shadow_size=1.65,shadow_softness=10,
 mid_selection=-1,mid_amount=38,mid_size=1.0,mid_softness=8,
 high_selection=-1,high_amount=18,high_size=.65,high_softness=6,seed=842317,film_format=1,engine_version=4,
 colour_grain=50.0,clumping=50.0,fine_balance=50.0,
 shadow_crossover=32.0,highlight_crossover=55.0,
 shadow_colour_grain=50.0,shadow_clumping=50.0,shadow_fine_balance=50.0,
 mid_colour_grain=50.0,mid_clumping=50.0,mid_fine_balance=50.0,
 high_colour_grain=50.0,high_clumping=50.0,high_fine_balance=50.0)
FLOAT_RANGES={'colour_grain':(0,100,1),'clumping':(0,100,1),'fine_balance':(0,100,1),'amount':(0,100,1),'size':(.5,2,.01),'softness':(0,40,1),
 'shadow_crossover':(10,65,1),'highlight_crossover':(15,90,1)}
for prefix in ('shadow','mid','high'):
 FLOAT_RANGES[prefix+'_amount']=(0,100,1)
 FLOAT_RANGES[prefix+'_size']=(.5,2,.01)
 FLOAT_RANGES[prefix+'_softness']=(0,40,1)
 for control in ('colour_grain','clumping','fine_balance'):
  FLOAT_RANGES[prefix+'_'+control]=(0,100,1)


def profiles():
 global _PROFILE_CACHE
 if _PROFILE_CACHE is None:
  _PROFILE_CACHE=json.loads(zlib.decompress(base64.b85decode(DATA.encode())))
 return _PROFILE_CACHE


def settings_from(data=None):
 s=DEFAULT.copy()
 if data and 'film_format' not in data:s['film_format']=0
 if data and 'engine_version' not in data:s['engine_version']=3
 if data:
  data=dict(data)
  for zone in ('shadow','mid','high'):
   for control in ('colour_grain','clumping','fine_balance'):
    target=zone+'_'+control
    if target not in data and control in data:data[target]=data[control]
 for k,v in (data or {}).items():
  if k not in s: continue
  try:
   if k in FLOAT_RANGES:
    lo,hi,_=FLOAT_RANGES[k];s[k]=max(lo,min(hi,float(v)))
   elif k=='film_format':s[k]=max(0,min(len(FILM_FORMATS)-1,int(v)))
   elif k=='engine_version':s[k]=max(3,min(4,int(v)))
   elif k=='seed': s[k]=max(0,min(2147483647,int(v)))
   else:s[k]=max(-1 if k.endswith('_selection') else 0,min(len(LABELS)-1,int(v)))
  except (TypeError,ValueError,OverflowError):pass
 if any(s[k]!=50 for k in DEFAULT if k.endswith(('_colour_grain','_clumping','_fine_balance'))) or any(s[k]!=DEFAULT[k] for k in ('shadow_crossover','highlight_crossover')):s['engine_version']=4
 return s


def _config_settings(config):
 return settings_from({k:config.get_property(k.replace('_','-')) for k in DEFAULT})


def _store_config(config,settings):
 for key,default in DEFAULT.items():
  value=settings[key]
  config.set_property(key.replace('_','-'),float(value) if isinstance(default,float) else int(round(value)))


class RenderCache:
 def __init__(self,budget=192*1024*1024):
  self.budget=budget;self.bytes=0;self.items=OrderedDict();self.lock=threading.RLock()
 def get(self,key):
  with self.lock:
   value=self.items.get(key)
   if value is not None:self.items.move_to_end(key)
   return value
 def put(self,key,value):
  if value.nbytes>self.budget:return value
  value=np.ascontiguousarray(value);value.setflags(write=False)
  with self.lock:
   old=self.items.pop(key,None)
   if old is not None:self.bytes-=old.nbytes
   self.items[key]=value;self.bytes+=value.nbytes
   while self.bytes>self.budget:
    _,old=self.items.popitem(last=False);self.bytes-=old.nbytes
  return value
 def clear(self):
  with self.lock:self.items.clear();self.bytes=0


def _fft_size(n):
 # Smooth FFT dimensions avoid the expensive prime-sized edge tiles.
 candidate=n
 while True:
  rest=candidate
  for factor in (2,3,5):
   while rest%factor==0:rest//=factor
  if rest==1:return candidate
  candidate+=1


def _normal_grid(x,y,w,h,seed,pair=False):
 # Global-coordinate white field, independent of tiles or render order.
 xx=np.arange(x,x+w,dtype=np.int64).astype(np.uint32)[None,:]
 yy=np.arange(y,y+h,dtype=np.int64).astype(np.uint32)[:,None]
 v=(xx*np.uint32(0x9e3779b1))^(yy*np.uint32(0x85ebca77))^np.uint32(seed)
 def mix(a):
  a=a^(a>>np.uint32(16));a=a*np.uint32(0x7feb352d)
  a=a^(a>>np.uint32(15));a=a*np.uint32(0x846ca68b)
  return a^(a>>np.uint32(16))
 u=(mix(v).astype(np.float32)+.5)/4294967296.0
 t=(mix(v^np.uint32(0x68bc21eb)).astype(np.float32)+.5)/4294967296.0
 radius=np.sqrt(-2*np.log(u));angle=2*np.pi*t
 field=(radius*np.cos(angle)).astype(np.float32)
 if pair:return np.stack((field,(radius*np.sin(angle)).astype(np.float32)))
 return field


def _smooth_field(x,y,w,h,seed,scale,pair=False,cache=None):
 # Continuous global coordinates keep the small colour/variance fields tile-safe.
 scale=max(1.0,float(scale))
 key=('smooth',x,y,w,h,seed,scale,pair)
 if cache is not None:
  cached=cache.get(key)
  if cached is not None:return cached
 if scale==1.0:
  field=_normal_grid(x,y,w,h,seed,pair)
  return cache.put(key,field) if cache is not None else field
 gx=np.arange(x,x+w,dtype=np.float64)/scale
 gy=np.arange(y,y+h,dtype=np.float64)/scale
 ix=np.floor(gx).astype(np.int64);iy=np.floor(gy).astype(np.int64)
 grid=_normal_grid(int(ix[0]),int(iy[0]),int(ix[-1]-ix[0]+2),int(iy[-1]-iy[0]+2),seed,pair)
 fx=(gx-ix).astype(np.float32);fy=(gy-iy).astype(np.float32)
 fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy)
 ix-=ix[0];iy-=iy[0]
 a=fx[None,:];b=fy[:,None]
 rows=grid[...,ix]*(1-fx)+grid[...,ix+1]*fx
 field=rows[...,iy,:]*(1-b)+rows[...,iy+1,:]*b
 field/=np.sqrt(((1-a)**2+a*a)*((1-b)**2+b*b))
 return cache.put(key,field) if cache is not None else field


def _cache_kernel(key,value):
 def size(v):return (v[0] if isinstance(v,tuple) else v).nbytes
 with KERNEL_LOCK:
  KERNEL_CACHE[key]=value;used=sum(size(v) for v in KERNEL_CACHE.values())
  while len(KERNEL_CACHE)>256 or used>64*1024*1024:
   _,old=KERNEL_CACHE.popitem(last=False);used-=size(old)
 return value


def _kernel(profile,size,softness):
 size=float(size);softness=round(softness)
 key=(profile,size,softness)
 with KERNEL_LOCK:
  cached=KERNEL_CACHE.get(key)
  if cached is not None:
   KERNEL_CACHE.move_to_end(key);return cached
 raw=np.asarray(profiles()[profile]['kernel'],np.float32)
 dim=int(min(96,max(16,round(32*size))))
 # Bilinear spatial resampling preserves measured 2D shape, including anisotropy.
 p=np.linspace(0,31,dim);i=np.floor(p).astype(int);j=np.minimum(i+1,31)
 a=(p-i).astype(np.float32)
 h0=raw[i[:,None],i[None,:]]*(1-a[:,None])*(1-a[None,:])
 h0+=raw[j[:,None],i[None,:]]*a[:,None]*(1-a[None,:])
 h0+=raw[i[:,None],j[None,:]]*(1-a[:,None])*a[None,:]
 h0+=raw[j[:,None],j[None,:]]*a[:,None]*a[None,:]
 # Extra softness without scipy; measured kernel still anchors the shape.
 sigma=float(softness)/20
 if sigma>.025:
  from numpy.fft import fft2,ifft2,fftfreq
  fy=fftfreq(dim)[:,None];fx=fftfreq(dim)[None,:]
  h0=ifft2(fft2(h0)*np.exp(-2*np.pi**2*sigma**2*(fx*fx+fy*fy))).real.astype(np.float32)
 h0-=h0.mean();h0/=max(np.sqrt(np.sum(h0*h0)),1e-9)
 return _cache_kernel(key,h0)


def _pixel_pitch(settings):
 width=FILM_WIDTHS[int(settings.get('film_format',0))]
 if not width:return 1.0
 return max(0.005,min(4.0,float(settings.get('_frame_pixels',width*1000/SCAN_PITCH_UM))*SCAN_PITCH_UM/(width*1000)))


def _scaled_kernel(kernel,factor):
 dim=max(3,min(192,int(round(kernel.shape[0]*factor))))
 p=np.linspace(0,kernel.shape[0]-1,dim);i=np.floor(p).astype(int)
 j=np.minimum(i+1,kernel.shape[0]-1);a=(p-i).astype(np.float32)
 out=kernel[i[:,None],i[None,:]]*(1-a[:,None])*(1-a[None,:])
 out+=kernel[j[:,None],i[None,:]]*a[:,None]*(1-a[None,:])
 out+=kernel[i[:,None],j[None,:]]*(1-a[:,None])*a[None,:]
 out+=kernel[j[:,None],j[None,:]]*a[:,None]*a[None,:]
 out-=out.mean();out/=max(float(np.sqrt(np.sum(out*out))),1e-9)
 return out


def _spectral_key(layered):
 if not isinstance(layered,tuple):return layered
 balance=layered[2] if len(layered)>2 else 50.0
 if layered[0]==0 and balance==50:return False
 return (layered[0],balance)


def _clump_strength(choice,value):
 baseline=TEXTURE_MODELS.get(choice,(0,0))[1]
 return baseline*value/50 if value<=50 else baseline+.45*(value-50)/50


def _kernel_info(profile,size,softness,pitch=1.0,layered=False):
 pitch=round(float(pitch),6);spectral=_spectral_key(layered)
 if pitch==1.0 and not spectral:return _kernel(profile,size,softness),1.0
 key=('physical',profile,float(size),round(softness),pitch,spectral)
 with KERNEL_LOCK:
  cached=KERNEL_CACHE.get(key)
  if cached is not None:KERNEL_CACHE.move_to_end(key);return cached
 base=_kernel(profile,size,softness)
 if spectral is True or (spectral and isinstance(layered,tuple) and layered[0]>0):
  # A restrained second spatial band for interpretations; measured stocks bypass it.
  fine=_kernel(profile,max(.5,size*.45),softness)
  dim=max(base.shape[0],fine.shape[0]);mixed=np.zeros((dim,dim),np.float32)
  fine_gain=layered[0] if isinstance(layered,tuple) else .14
  coarse_gain=math.sqrt(1-fine_gain*fine_gain) if isinstance(layered,tuple) else .99
  for component,gain in ((base,coarse_gain),(fine,fine_gain)):
   margin=(dim-component.shape[0])//2
   mixed[margin:margin+component.shape[0],margin:margin+component.shape[1]]+=gain*component
  base=mixed/max(float(np.sqrt(np.sum(mixed*mixed))),1e-9)
 balance=layered[2] if isinstance(layered,tuple) and len(layered)>2 else 50.0
 if balance!=50:
  target=_scaled_kernel(base,.5 if balance<50 else 1.8)
  dim=max(base.shape[0],target.shape[0]);mixed=np.zeros((dim,dim),np.float32)
  angle=abs(balance-50)*math.pi/100
  for component,gain in ((base,math.cos(angle)),(target,math.sin(angle))):
   margin=(dim-component.shape[0])//2
   mixed[margin:margin+component.shape[0],margin:margin+component.shape[1]]+=gain*component
  base=mixed/max(float(np.sqrt(np.sum(mixed*mixed))),1e-9)
 footprint=1/pitch
 # Gaussian approximation to the additional square-pixel aperture. The measured
 # scanner kernel already includes its native one-pixel aperture.
 aperture=math.sqrt(max(0,footprint*footprint-1)/12)
 if aperture>.001:
  margin=int(math.ceil(4*aperture));dim=_fft_size(base.shape[0]+2*margin)
  padded=np.zeros((dim,dim),np.float32);offset=(dim-base.shape[0])//2
  padded[offset:offset+base.shape[0],offset:offset+base.shape[1]]=base
  from numpy.fft import fft2,ifft2,fftfreq
  fy=fftfreq(dim)[:,None];fx=fftfreq(dim)[None,:]
  base=ifft2(fft2(padded)*np.exp(-2*np.pi**2*aperture**2*(fx*fx+fy*fy))).real.astype(np.float32)
 attenuation=min(1.0,float(np.sqrt(np.sum(base*base))))
 dim=max(3,min(768 if balance!=50 else 256,int(round(base.shape[0]*pitch))))
 coordinate=(np.arange(dim,dtype=np.float32)-(dim-1)/2)/pitch+(base.shape[0]-1)/2
 i=np.floor(coordinate).astype(int);j=i+1;a=coordinate-i
 valid=(coordinate>=0)&(coordinate<=base.shape[0]-1)
 i=np.clip(i,0,base.shape[0]-1);j=np.clip(j,0,base.shape[0]-1)
 kernel=base[i[:,None],i[None,:]]*(1-a[:,None])*(1-a[None,:])
 kernel+=base[j[:,None],i[None,:]]*a[:,None]*(1-a[None,:])
 kernel+=base[i[:,None],j[None,:]]*(1-a[:,None])*a[None,:]
 kernel+=base[j[:,None],j[None,:]]*a[:,None]*a[None,:]
 kernel*=valid[:,None]*valid[None,:];kernel-=kernel.mean()
 norm=float(np.sqrt(np.sum(kernel*kernel)))
 if norm<1e-9:
  kernel=np.zeros((3,3),np.float32);kernel[1,1]=1;norm=1
 kernel=np.asarray(kernel/norm,np.float32)
 value=(kernel,attenuation)
 return _cache_kernel(key,value)


def _zone_model(lum,d,weights,s,index,pitch):
 zone=('shadow','mid','high')[index];choice=int(s[zone+'_selection'])
 if choice<0:choice=int(s['selection'])
 key=KEYS[choice];p=profiles()[key];spec=PROFILE_SPECS[choice]
 size=s['size']*s[zone+'_size']*spec['scale'];soft=s['softness']+s[zone+'_softness']
 layered=spec['kind']!='Measured'
 if s.get('engine_version',3)>=4:
  fine=TEXTURE_MODELS[choice][0] if layered else 0.0
  clump=_clump_strength(choice,s.get(zone+'_clumping',50))
  balance=s.get(zone+'_fine_balance',50)
  if layered or clump>0 or balance!=50:layered=(fine,clump,balance)
 _,attenuation=_kernel_info(key,size,soft,pitch,layered)
 sigma=np.interp(d,p['d'],p['sigma']).astype(np.float32)
 skew=np.interp(d,p['d'],p['skew']).astype(np.float32)
 gain=1.0 if spec['kind']=='Measured' else spec['gain']
 if spec['kind']=='Sensitometry':
  curve=np.asarray(SENSITOMETRY_CURVES[choice],np.float32)
  gain=gain*(.65+.35*np.interp(lum,np.linspace(0,1,len(curve)),curve))
 elif spec['kind']!='Measured':
  t=spec['tone'];gain=gain*(t[0]*weights[0]+t[1]*weights[1]+t[2]*weights[2])
 coefficient=sigma*weights[index]*(s[zone+'_amount']/50)*(s['amount']/35)*gain*attenuation
 asymmetry=np.clip(skew*attenuation/6,-.20,.20)
 return key,size,soft,layered,coefficient,asymmetry


def _tone_bias(settings,pitch):
 # Small one-dimensional expectation LUT; independent of seed, tile and image data.
 key=(pitch,tuple(sorted((k,v) for k,v in settings.items() if k not in ('seed','_frame_pixels','colour_grain') and not k.endswith('_colour_grain'))))
 with KERNEL_LOCK:
  cached=BIAS_CACHE.get(key)
  if cached is not None:BIAS_CACHE.move_to_end(key);return cached
 lum=np.linspace(0,1,1025,dtype=np.float32);d=.03+2.57*lum**(1/1.45)
 weights=_weights(lum,settings);variance=np.zeros_like(lum);third=np.zeros_like(lum)
 for index,zone in enumerate(('shadow','mid','high')):
  if settings[zone+'_amount']==0:continue
  _,_,_,_,coefficient,a=_zone_model(lum,d,weights,settings,index,pitch)
  variance+=coefficient*coefficient
  third+=coefficient**3*(6*a+8*a**3)/(1+2*a*a)**1.5
 sigma=np.sqrt(variance)
 effective=np.clip(third/(np.maximum(sigma,1e-9)**3*6),-.20,.20)
 nodes,prob=np.polynomial.hermite.hermgauss(16);z=(nodes*math.sqrt(2))[:,None]
 field=(z+effective[None,:]*(z*z-1))/np.sqrt(1+2*effective[None,:]**2)
 value=np.maximum((d[None,:]+sigma[None,:]*field-.03)/2.57,0)**1.45
 probability=(prob/math.sqrt(math.pi))[:,None]
 low=np.full_like(lum,-1);high=np.maximum(1,value.max(axis=0))
 for _ in range(20):
  middle=(low+high)/2
  mean=np.sum(probability*np.clip(value-middle[None,:],0,1),axis=0)
  low=np.where(mean>lum,middle,low);high=np.where(mean>lum,high,middle)
 bias=np.asarray((low+high)/2,np.float32)
 bias[variance<1e-15]=0
 active=[(z,int(settings[z+'_selection'] if settings[z+'_selection']>=0 else settings['selection']))
         for z in ('shadow','mid','high') if settings[z+'_amount']]
 if any(_clump_strength(choice,settings[z+'_clumping'])!=_clump_strength(choice,50) for z,choice in active):
   bias=_clump_bias(settings,pitch,lum,d,weights,variance,bias)
 result=(lum,bias)
 with KERNEL_LOCK:
  BIAS_CACHE[key]=result
  while len(BIAS_CACHE)>16:BIAS_CACHE.popitem(last=False)
 return result


def _clump_bias(s,pitch,lum,d,weights,variance,bias):
 sigma=np.sqrt(variance);moments=[]
 for control in (False,True):
  third=np.zeros_like(lum);fourth=np.zeros_like(lum)
  for index,zone in enumerate(('shadow','mid','high')):
   if s[zone+'_amount']==0:continue
   *_,coefficient,a=_zone_model(lum,d,weights,s,index,pitch)
   choice=s[zone+'_selection']
   if choice<0:choice=s['selection']
   c=_clump_strength(int(choice),s[zone+'_clumping'] if control else 50)
   third+=coefficient**3*(6*a+8*a**3)/(1+2*a*a)**1.5*math.exp(1.5*c*c)
   fourth+=coefficient**4*((3+60*a*a+60*a**4)/(1+2*a*a)**2*math.exp(4*c*c)-3)
  m3=third/np.maximum(sigma,1e-9)**3
  m4=3+fourth/np.maximum(variance,1e-15)**2
  c2=np.zeros_like(lum)
  for _ in range(3):
   a=np.clip(m3/(6*np.exp(1.5*c2)),-.20,.20)
   base=(3+60*a*a+60*a**4)/(1+2*a*a)**2
   c2=np.clip(.25*np.log(np.maximum(m4/base,1)),0,.8**2)
  moments.append((a,np.sqrt(c2)))
 nodes,prob=np.polynomial.hermite.hermgauss(12)
 env_nodes,env_prob=np.polynomial.hermite.hermgauss(8)
 z=(nodes*math.sqrt(2))[:,None,None];g=(env_nodes*math.sqrt(2))[None,:,None]
 probability=(prob[:,None]*env_prob[None,:]/math.pi)[:,:,None]
 values=[]
 for a,c in moments:
  field=(z+a*(z*z-1))/np.sqrt(1+2*a*a)*np.exp(c*g-c*c)
  values.append(np.maximum((d+sigma*field-.03)/2.57,0)**1.45)
 target=np.sum(probability*np.clip(values[0]-bias,0,1),axis=(0,1))
 corrected=bias.astype(np.float64)
 for _ in range(4):
  shifted=values[1]-corrected
  error=np.sum(probability*np.clip(shifted,0,1),axis=(0,1))-target
  slope=np.sum(probability*((shifted>0)&(shifted<1)),axis=(0,1))
  corrected+=np.clip(error/np.maximum(slope,.05),-.05,.05)
 corrected[variance<1e-15]=0
 return corrected.astype(np.float32)


def _fft_functions():
 global FFT_API,FFT_BACKEND
 with KERNEL_LOCK:
  if FFT_API is None:
   try:
    from scipy.fft import rfft2,irfft2
    FFT_API=(lambda a:rfft2(a,workers=1),lambda a,s:irfft2(a,s=s,workers=1))
    FFT_BACKEND='SciPy'
   except Exception:
    from numpy.fft import rfft2,irfft2
    FFT_API=(rfft2,irfft2);FFT_BACKEND='NumPy'
 return FFT_API


def _worker_limit():
 return min(4,max(1,(os.cpu_count() or 1)//2))


def _tiles(width,height,bounds=None):
 x0,y0,x1,y1=(0,0,width,height) if bounds is None else bounds
 x0=max(0,min(width,x0));x1=max(0,min(width,x1))
 y0=max(0,min(height,y0));y1=max(0,min(height,y1))
 if x1<=x0 or y1<=y0:return []
 return [(x,y,min(TILE_W,width-x),min(TILE_H,height-y))
         for y in range((y0//TILE_H)*TILE_H,y1,TILE_H)
         for x in range((x0//TILE_W)*TILE_W,x1,TILE_W)]


def _render_jobs(jobs,settings,read,write,cache=None,progress=None,workers=None,selection=None):
 cancel=threading.Event();pending=deque();iterator=iter(jobs);done=0
 count=max(1,workers or _worker_limit())
 if settings.get('engine_version',3)>=4 and settings['amount']:_response_model(settings,_pixel_pitch(settings))
 with ThreadPoolExecutor(max_workers=count) as pool:
  try:
   exhausted=False
   while pending or not exhausted:
    while not exhausted and len(pending)<count:
     job=next(iterator,None)
     if job is None:exhausted=True;break
     x,y,w,h=job;pixels=read(x,y,w,h)
     mask=selection(x,y,w,h) if selection is not None else None
     if mask is not None and not np.any(mask):future=None
     else:future=pool.submit(render_tile,pixels,x,y,settings,cache,cancel)
     pending.append((job,future,pixels if mask is not None else None,mask))
    if pending:
     (x,y,w,h),future,original,mask=pending.popleft()
     pixels=future.result() if future is not None else original
     if mask is not None and future is not None:
      pixels[:,:,:3]=original[:,:,:3]+(pixels[:,:,:3]-original[:,:,:3])*mask[:,:,None]
     write(x,y,pixels);done+=1
     if progress is not None:progress(done/len(jobs))
  except BaseException:
   cancel.set()
   for _,future,_,_ in pending:
    if future is not None:future.cancel()
   raise


def _shaped(x,y,w,h,key,size,softness,seed,cache=None,pitch=1.0,layered=False):
 rfft2,irfft2=_fft_functions()
 size=float(size);softness=round(softness);spectral=_spectral_key(layered)
 field_key=('field',x,y,w,h,key,size,softness,seed,round(pitch,6),spectral)
 if cache is not None:
  cached=cache.get(field_key)
  if cached is not None:return cached
 kernel,_=_kernel_info(key,size,softness,pitch,layered)
 halo=max(8,int(math.ceil(max(kernel.shape)/2))+2);W=_fft_size(w+2*halo);H=_fft_size(h+2*halo)
 spectrum_key=('spectrum',H,W,key,size,softness,round(pitch,6),spectral)
 spectrum=cache.get(spectrum_key) if cache is not None else None
 if spectrum is None:
  kh,kw=kernel.shape
  pad=np.zeros((H,W),np.float32);pad[:kh,:kw]=kernel
  pad=np.roll(pad,(-(kh//2),-(kw//2)),axis=(0,1))
  spectrum=rfft2(pad)
  if cache is not None:spectrum=cache.put(spectrum_key,spectrum)
 noise=_normal_grid(x-halo,y-halo,W,H,seed)
 result=irfft2(rfft2(noise)*spectrum,s=(H,W)).astype(np.float32)
 field=result[halo:halo+h,halo:halo+w].copy()
 return cache.put(field_key,field) if cache is not None else field


def _weights(lum,settings=None):
 # Crossover = 50% mask strength on the encoded-sRGB brightness scale.
 s=DEFAULT if settings is None else settings
 sh=np.clip((s['shadow_crossover']/100+.20-lum)/.40,0,1)
 hi=np.clip((lum-s['highlight_crossover']/100+.21)/.42,0,1)
 sh=sh*sh*(3-2*sh);hi=hi*hi*(3-2*hi)
 overlap=np.maximum(sh+hi,1)
 sh=sh/overlap;hi=hi/overlap
 mid=np.maximum(0,1-sh-hi)
 return (sh,mid,hi)


def _tonal_luma(rgb):
 return np.clip(.2126*rgb[:,:,0]+.7152*rgb[:,:,1]+.0722*rgb[:,:,2],0,1)


def _zone_mask_tile(src,settings,selection):
 result=src.copy()
 index=int(settings['_inspect_zone'])
 lum=_tonal_luma(np.clip(src[:,:,:3],0,1))
 weight=_weights(lum,settings)[index]
 if selection is None:
  result[:,:,:3]=weight[:,:,None]
 else:
  result[:,:,:3]=src[:,:,:3]*(1-selection[:,:,None])+weight[:,:,None]*selection[:,:,None]
 return result


def _render_exact(src,x,y,s,cache=None,cancel=None):
 h,w=src.shape[:2];out=src.copy()
 if cancel is not None and cancel.is_set():raise CancelledError()
 if s['amount']==0:return out
 rgb=np.clip(src[:,:,:3],0,1)
 lum=np.clip(.2126*rgb[:,:,0]+.7152*rgb[:,:,1]+.0722*rgb[:,:,2],0,1)
 d=.03+2.57*np.power(lum,1/1.45);weights=_weights(lum,s);pitch=_pixel_pitch(s)
 delta=np.zeros((h,w),np.float32)
 chroma=None
 for index,(zone,weight) in enumerate(zip(('shadow','mid','high'),weights)):
  if cancel is not None and cancel.is_set():raise CancelledError()
  if s[zone+'_amount']==0 or not np.any(weight):continue
  key,size,soft,layered,coefficient,a=_zone_model(lum,d,weights,s,index,pitch)
  field=_shaped(x,y,w,h,key,size,soft,s['seed']+index*1009,cache,pitch,layered)
  if cancel is not None and cancel.is_set():raise CancelledError()
  field=(field+a*(field*field-1))/np.sqrt(1+2*a*a)
  envelope=None
  if isinstance(layered,tuple) and layered[1]>0:
   clump=layered[1]
   envelope=np.exp(clump*_smooth_field(x,y,w,h,s['seed']+index*1009+25001,5*size*pitch,cache=cache)-clump*clump)
   field=field*envelope
  delta+=field*coefficient
  choice=int(s[zone+'_selection'])
  if choice<0:choice=int(s['selection'])
  if s.get('engine_version',3)>=4 and choice in COLOUR_MODELS and s.get(zone+'_colour_grain',50)>0:
   strength,red,blue,scale=COLOUR_MODELS[choice]
   strength*=6.0*_colour_gain(s.get(zone+'_colour_grain',50))
   spacing=max(1.0,size*pitch*scale*(1+soft/40))
   pair=_smooth_field(x,y,w,h,s['seed']+index*1009+51001,spacing,pair=True,cache=cache)
   u=pair[0]*red;v=pair[1]*blue
   v=.25*u+math.sqrt(.9375)*v
   if chroma is None:chroma=np.zeros((h,w,3),np.float32)
   # Opponent perturbations retain Rec.709 luminance before gamut clipping.
   amplitude=coefficient*strength*(1.45/2.57)*np.maximum(lum,.001)**(1-1/1.45)
   if envelope is not None:amplitude=amplitude*envelope
   chroma[:,:,0]+=u*amplitude
   chroma[:,:,1]-=(.2126*u+.0722*v)*amplitude/.7152
   chroma[:,:,2]+=v*amplitude
 positive=np.maximum((d+delta-.03)/2.57,0)**1.45
 axis,bias=_tone_bias(s,pitch)
 if cancel is not None and cancel.is_set():raise CancelledError()
 shift=(positive-lum-np.interp(lum,axis,bias)).astype(np.float32)
 result=rgb+shift[:,:,None]
 if chroma is not None:result+=chroma
 out[:,:,:3]=np.clip(result,0,1);out[:,:,3]=src[:,:,3]
 return out


def _response_model(s,pitch):
 with MODEL_LOCK:
  key=(pitch,tuple(sorted((k,v) for k,v in s.items() if k not in ('seed','_frame_pixels','colour_grain') and not k.endswith('_colour_grain'))))
  with KERNEL_LOCK:
   cached=RESPONSE_CACHE.get(key)
   if cached is not None:RESPONSE_CACHE.move_to_end(key);return cached
  lum=np.linspace(0,1,16385,dtype=np.float32)
  d=.03+2.57*lum**(1/1.45);weights=_weights(lum,s);zones=[]
  for index in range(3):
   if s[('shadow','mid','high')[index]+'_amount']==0:
    zones.append(None);continue
   model=_zone_model(lum,d,weights,s,index,pitch)
   zones.append(model)
  axis,bias=_tone_bias(s,pitch)
  result=(zones,np.interp(lum,axis,bias).astype(np.float32))
  with KERNEL_LOCK:
   RESPONSE_CACHE[key]=result
   while len(RESPONSE_CACHE)>8:RESPONSE_CACHE.popitem(last=False)
  return result


def _lookup(table,index,fraction):
 left=table[index]
 return left+(table[index+1]-left)*fraction


def render_tile(src,x,y,s,cache=None,cancel=None):
 if s.get('engine_version',3)<4:return _render_exact(src,x,y,s,cache,cancel)
 if cancel is not None and cancel.is_set():raise CancelledError()
 out=src.copy()
 if s['amount']==0 or not any(s[z+'_amount'] for z in ('shadow','mid','high')):return out
 h,w=src.shape[:2];rgb=out[:,:,:3];np.clip(rgb,0,1,out=rgb)
 lum=np.clip(.2126*rgb[:,:,0]+.7152*rgb[:,:,1]+.0722*rgb[:,:,2],0,1)
 coordinate=lum*16384;index=np.minimum(coordinate.astype(np.intp),16383);fraction=(coordinate-index).astype(np.float32)
 pitch=_pixel_pitch(s);zones,bias=_response_model(s,pitch)
 delta=np.zeros((h,w),np.float32);red_delta=None;blue_delta=None;colour_slope=None
 for k,zone in enumerate(('shadow','mid','high')):
  if cancel is not None and cancel.is_set():raise CancelledError()
  if s[zone+'_amount']==0:continue
  key,size,soft,layered,coefficient_table,asymmetry_table=zones[k]
  coefficient=_lookup(coefficient_table,index,fraction).astype(np.float32)
  if not np.any(coefficient):continue
  a=_lookup(asymmetry_table,index,fraction).astype(np.float32)
  field=_shaped(x,y,w,h,key,size,soft,s['seed']+k*1009,cache,pitch,layered)
  if cancel is not None and cancel.is_set():raise CancelledError()
  field=(field+a*(field*field-1))/np.sqrt(1+2*a*a)
  envelope=None
  if isinstance(layered,tuple) and layered[1]>0:
   clump=layered[1]
   envelope=_smooth_field(x,y,w,h,s['seed']+k*1009+25001,5*size*pitch,cache=cache)
   envelope=np.exp(clump*envelope-clump*clump)
   field*=envelope
  delta+=field*coefficient
  choice=int(s[zone+'_selection'])
  if choice<0:choice=int(s['selection'])
  if choice in COLOUR_MODELS and s.get(zone+'_colour_grain',50)>0:
   strength,red,blue,scale=COLOUR_MODELS[choice]
   strength*=6.0*_colour_gain(s.get(zone+'_colour_grain',50))
   spacing=max(1.0,size*pitch*scale*(1+soft/40))
   pair=_smooth_field(x,y,w,h,s['seed']+k*1009+51001,spacing,pair=True,cache=cache)
   u=pair[0]*red;v=.25*u+math.sqrt(.9375)*pair[1]*blue
   if colour_slope is None:colour_slope=(1.45/2.57)*np.maximum(lum,.001)**(1-1/1.45)
   amplitude=coefficient*strength*colour_slope
   if envelope is not None:amplitude*=envelope
   if red_delta is None:red_delta=u*amplitude;blue_delta=v*amplitude
   else:red_delta+=u*amplitude;blue_delta+=v*amplitude
 d=.03+2.57*np.power(lum,1/1.45)
 positive=np.maximum((d+delta-.03)/2.57,0)**1.45
 shift=(positive-lum-_lookup(bias,index,fraction)).astype(np.float32)
 if cancel is not None and cancel.is_set():raise CancelledError()
 if red_delta is None:out[:,:,:3]=np.clip(rgb+shift[:,:,None],0,1)
 else:
  out[:,:,0]=np.clip(rgb[:,:,0]+shift+red_delta,0,1)
  out[:,:,1]=np.clip(rgb[:,:,1]+shift-(.2126*red_delta+.0722*blue_delta)/.7152,0,1)
  out[:,:,2]=np.clip(rgb[:,:,2]+shift+blue_delta,0,1)
 return out


def _sample_tile(w,h,tone,settings,cache,cancel):
 rgb=np.empty((h,w,3),np.uint8)
 for row in range(0,h,TILE_H):
  for col in range(0,w,TILE_W):
   if cancel is not None and cancel.is_set():raise CancelledError()
   th,tw=min(TILE_H,h-row),min(TILE_W,w-col)
   sample=np.full((th,tw,4),tone,np.float32);sample[:,:,3]=1
   pixels=render_tile(sample,col,row,settings,cache,cancel)[:,:,:3]
   rgb[row:row+th,col:col+tw]=np.clip(pixels*255,0,255).astype(np.uint8)
 return rgb.tobytes()


def _render_preview_tile(src,x,y,settings,cache,cancel,selection):
 if cancel is not None and cancel.is_set():raise CancelledError()
 if selection is not None and not np.any(selection):return src.copy()
 if '_inspect_zone' in settings:return _zone_mask_tile(src,settings,selection)
 pixels=render_tile(src,x,y,settings,cache,cancel)
 if selection is not None:
  pixels[:,:,:3]=src[:,:,:3]+(pixels[:,:,:3]-src[:,:,:3])*selection[:,:,None]
 return pixels


def rect(x,y,w,h):
 r=Gegl.Rectangle();r.x,r.y,r.width,r.height=map(int,(x,y,w,h));return r

def read_pixels(buffer,x,y,w,h):
 raw=buffer.get(rect(x,y,w,h),1.0,FORMAT,Gegl.AbyssPolicy.NONE)
 return np.frombuffer(bytes(raw),np.float32).reshape(h,w,4).copy()

def write_pixels(buffer,x,y,pixels):
 h,w=pixels.shape[:2]
 buffer.set(rect(x,y,w,h),FORMAT,np.ascontiguousarray(pixels,np.float32).tobytes())

def read_selection(buffer,x,y,w,h):
 raw=buffer.get(rect(x,y,w,h),1.0,'Y float',Gegl.AbyssPolicy.NONE)
 return np.clip(np.frombuffer(bytes(raw),np.float32).reshape(h,w).copy(),0,1)

def _can_render_layer(layer):
 return (isinstance(layer,Gimp.Layer) and not layer.is_group()
         and not any(getattr(layer,name,lambda:False)()
                     for name in ('is_text_layer','is_link_layer','is_vector_layer')))


def _recipe(layer):
 v=layer.get_parasite(PARASITE)
 if v is None:return None
 try:
  recipe=json.loads(bytes(v.get_data()).rstrip(b'\0'))
  if not isinstance(recipe,dict) or not isinstance(recipe.get('settings'),dict):raise ValueError()
  tattoo=recipe.get('source_tattoo')
  if not isinstance(tattoo,int) or isinstance(tattoo,bool) or tattoo<=0:raise ValueError()
  return recipe
 except (ValueError,TypeError):
  raise ValueError('The grain layer has invalid saved settings. Select the original source layer instead.')


def _copy_attributes(source,target):
 for name in ('opacity','mode','blend_space','composite_space','composite_mode'):
  if not getattr(target,'set_'+name)(getattr(source,'get_'+name)()):
   raise RuntimeError('Could not preserve layer '+name.replace('_',' ')+'.')
 mask=source.get_mask()
 if mask is not None:
  if target.get_mask() is None:
   copied=target.create_mask(Gimp.AddMaskType.WHITE)
   if copied is None or not target.add_mask(copied):raise RuntimeError('Could not preserve layer mask.')
   src=mask.get_buffer();dst=copied.get_buffer();w,h=mask.get_width(),mask.get_height()
   for y in range(0,h,256):
    for x in range(0,w,384):
     rw,rh=min(384,w-x),min(256,h-y)
     dst.set(rect(x,y,rw,rh),'Y float',read_selection(src,x,y,rw,rh).tobytes())
   dst.flush();copied.update(0,0,w,h)
  for name in ('apply_mask','show_mask','edit_mask'):
   if not getattr(target,'set_'+name)(getattr(source,'get_'+name)()):
    raise RuntimeError('Could not preserve layer mask settings.')

def _attach(layer,source,s):
 data=json.dumps(dict(source_tattoo=source.get_tattoo(),settings={k:v for k,v in s.items() if not k.startswith('_')},engine_version=s.get('engine_version',3))).encode()+b'\0'
 if not layer.attach_parasite(Gimp.Parasite.new(PARASITE,Gimp.PARASITE_PERSISTENT|Gimp.PARASITE_UNDOABLE,data)):
  raise RuntimeError('Could not save grain layer settings.')

def _notice(parent,title,detail,kind=Gtk.MessageType.ERROR):
 d=Gtk.MessageDialog(transient_for=parent,modal=True,message_type=kind,
                     buttons=Gtk.ButtonsType.CLOSE,text=title)
 d.format_secondary_text(detail);d.run();d.destroy()

def _check(parent):
 try:
  if np is None:raise RuntimeError(NUMPY_ERROR)
  t=np.full((8,8,4),.5,np.float32);t[:,:,3]=1
  for index in range(len(LABELS)):
   setting=settings_from();setting['selection']=index
   if not np.isfinite(render_tile(t,0,0,setting)).all():
    raise RuntimeError('Invalid pixels from '+LABELS[index])
  status='Working';body=f'NumPy {np.__version__} · FFT: {FFT_BACKEND} · {_worker_limit()} workers · {len(LABELS)} grain choices · {len(profiles())} embedded scan profiles · Python {sys.executable}'
 except Exception as e:
  status='Missing or broken'
  body=(f'{e}\nPython: {sys.executable}\n\n'
        'Install NumPy for the Python interpreter that GIMP actually runs. '
        'On Linux, use the distribution package (for example python3-numpy), '
        'or GIMP’s bundled Python environment if applicable. On Windows and '
        'macOS, use the Python environment supplied with that GIMP installation. '
        'Restart GIMP and run Check Installation again.')
 _notice(parent,'Check Installation: '+status,body,Gtk.MessageType.INFO if status=='Working' else Gtk.MessageType.WARNING)

class GrainDialog(Gtk.Dialog):
 def __init__(self,image,source,initial,editing):
  super().__init__(title=NAME)
  GimpUi.window_set_transient(self)
  self.image=image;self.source=source;self.s=settings_from(initial);self.editing=editing
  self.display_base=editing if editing else source
  self.original_visibility=[(layer,layer.get_visible()) for layer in ((source,editing) if editing else (source,))]
  self.s['_frame_pixels']=max(source.get_width(),source.get_height())
  self.layer_options={};self.target_busy=False
  self.controls={};self.spins=[];self.combos={};self.combo_rows={};self.combo_labels={};self.choice_buttons={};self.pads={};self.pad_zooms={};self.readouts={};self.character_rows={};self.busy=False;self.timer=None;self.preview=None
  self.live=False;self.render_generation=0
  profiles()
  self.cache=RenderCache();self.sample_cache=RenderCache(12*1024*1024)
  self.sample_due={};self.sample_images={};self.show_before=False;self.before_key=False;self.before_mouse=False
  self.active_pad='shadow_';self.zone_prefix='shadow_';self.pad_action=None
  self.worker_count=_worker_limit()
  self.pool=ThreadPoolExecutor(max_workers=self.worker_count)
  self.cancel_render=threading.Event();self.pending={};self.jobs=deque()
  self.poll_id=None;self.preview_done=0;self.preview_total=0
  self.srcbuf=None;self.preview_buffer=None;self.closed=False;self.preview_frozen=False
  self.sample_pool=ThreadPoolExecutor(max_workers=1)
  self.sample_cancel=threading.Event();self.sample_requests={};self.sample_pending=None
  self.sample_poll_id=None;self.sample_turn=None
  self.preview_dirty=[];self.last_publish=0;self.dragging=False;self.menu_height=380
  self.set_border_width(10)
  self.set_default_size(1040,800)
  display=Gdk.Display.get_default()
  if display:
   seat=display.get_default_seat();pointer=seat.get_pointer() if seat else None
   if pointer:
    _,px,py=pointer.get_position();monitor=display.get_monitor_at_point(px,py)
   else:monitor=display.get_primary_monitor()
   if monitor:
    work=monitor.get_workarea()
    self.menu_height=min(420,max(120,int(work.height*.45)))
    self.set_default_size(min(1040,int(work.width*.9)),int(work.height*.8))
  self.set_resizable(True)
  reset=self.add_button('Reset',1)
  reset.set_tooltip_text('Restore the default grain and tonal controls without affecting any layer.')
  quit_button=self.add_button('Quit',Gtk.ResponseType.CLOSE)
  quit_button.set_tooltip_text('Close Analogue Grain. All previously applied result layers remain in the image.')
  apply=self.add_button('Apply to Layer',Gtk.ResponseType.APPLY)
  apply.set_tooltip_text('Apply the current settings to the Target layer as a new undoable result; keep this window open for another layer.')
  self.action_buttons=(reset,quit_button,apply)
  apply.get_style_context().add_class('suggested-action')
  self.get_style_context().add_class('analogue-grain')
  css=Gtk.CssProvider()
  css.load_from_data(b"""
.analogue-grain {background: #262626; color: #f6f6f6;}
.analogue-grain label {color: #f6f6f6;}
.analogue-grain .grain-heading {color: #cdcdcd; font-weight: bold; font-size: 14px;}
.analogue-grain .grain-guide {color: #d2d2d2; font-size: 13px;}
.analogue-grain .grain-readout {color: #ebebeb; font-weight: bold;}
.analogue-grain spinner {color: #bfbfbf;}
.analogue-grain button {background: #3d3d3d; color: #f6f6f6; border: 1px solid #6d6d6d; border-radius: 6px; padding: 7px;}
.analogue-grain button:hover {background: #515151; border-color: #c6c6c6;}
.analogue-grain button:checked {background: #6e6e6e; color: white; border-color: #cacaca; box-shadow: none;}
.analogue-grain button.grain-picker {background: #363636; border-color: #979797;}
.analogue-grain notebook {background: #2e2e2e; border: 1px solid #6d6d6d; border-radius: 7px;}
.analogue-grain notebook header {background: #282828;}
.analogue-grain notebook tab {background: #3a3a3a; border-radius: 5px; padding: 8px 14px; margin: 3px;}
.analogue-grain notebook tab:checked {background: #6e6e6e; box-shadow: none;}
.analogue-grain notebook tab:checked label {color: white; font-weight: bold;}
.analogue-grain scale trough {background: #212121; min-height: 6px; border: 1px solid #6d6d6d; border-radius: 5px;}
.analogue-grain scale highlight {background: #a0a0a0; border-radius: 4px;}
.analogue-grain scale slider {background: #bfbfbf; border: 1px solid #ededed; min-width: 14px; min-height: 14px; border-radius: 50%;}
.analogue-grain spinbutton {background: #373737; color: white; border: 1px solid #6d6d6d;}
.analogue-grain spinbutton entry {color: white; background: #373737;}
.analogue-grain button.suggested-action {background: #797979; color: white; border-color: #c6c6c6; box-shadow: none;}
.grain-popover {background: #2f2f2f; color: #f8f8f8; border: 1px solid #bababa;}
.grain-popover label {color: #f8f8f8;}
.grain-popover .grain-heading {color: #d2d2d2; font-weight: bold; padding: 8px 3px 4px 3px;}
.grain-popover button {background: transparent; color: #f8f8f8; border: 1px solid transparent; border-radius: 4px; padding: 7px;}
.grain-popover button:hover {background: #505050; border-color: #9f9f9f;}
.grain-popover button.selected {background: #6e6e6e; border-color: #d1d1d1;}
.grain-popover separator {background: #868686; min-height: 1px; margin: 7px 0;}

.analogue-grain checkbutton check:checked {background: #777777; color: #ffffff; border-color: #cccccc;}
.analogue-grain :focus {outline-color: #bbbbbb;}
.analogue-grain scrollbar.vertical {min-width: 22px; background: #262626;}
.analogue-grain scrollbar.vertical trough {min-width: 20px; background: #303030; border-radius: 8px;}
.analogue-grain scrollbar.vertical slider {min-width: 14px; min-height: 42px; margin: 3px; background: #8b8b8b; border-radius: 7px;}
.analogue-grain scrollbar.vertical slider:hover {background: #b8b8b8;}
.analogue-grain progressbar {min-height: 22px;}
.analogue-grain progressbar trough {min-height: 18px; border-radius: 5px; background: #474747; border: 1px solid #777777;}
.analogue-grain progressbar progress {min-height: 18px; border-radius: 4px; background: #78aee0; border: 1px solid #b4d4f2;}
.analogue-grain progressbar text {color: #ffffff; font-weight: bold;}
""")
  Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(),css,
                                           Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
  self._css=css
  self.connect('response',self._response)
  self.connect('key-press-event',self._before_key_event,True)
  self.connect('key-release-event',self._before_key_event,False)
  self.connect('focus-out-event',self._before_focus_out)
  root=self.get_content_area();root.set_spacing(7)
  scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
  scroll.set_overlay_scrolling(False);scroll.set_margin_end(8)
  scrollbar=scroll.get_vscrollbar();scrollbar.set_margin_start(8);scrollbar.set_margin_end(4)
  columns=Gtk.Paned.new(Gtk.Orientation.HORIZONTAL);self.columns=columns
  root.pack_start(columns,True,True,0)
  columns.pack1(scroll,False,False);columns.set_position(410)
  grey_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
  grey_box.set_border_width(6);columns.pack2(grey_box,True,False)
  self._grey_preview(grey_box)
  box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);box.set_border_width(4)
  scroll.add(box)
  self._layer_controls(box)
  self._selector(box,'selection',False)
  self.control_tabs=Gtk.Notebook();self.control_tabs.set_tab_pos(Gtk.PositionType.BOTTOM)
  box.pack_start(self.control_tabs,False,False,0)
  main_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);main_box.set_border_width(6)
  self.control_tabs.append_page(main_box,Gtk.Label(label='Main grain'))
  self._pad(main_box,'')
  advanced_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);advanced_box.set_border_width(6)
  self.control_tabs.append_page(advanced_box,Gtk.Label(label='Advanced'))
  format_row=Gtk.Box(spacing=8);format_row.set_border_width(6);advanced_box.pack_start(format_row,False,False,0)
  format_row.pack_start(Gtk.Label(label='Film format'),False,False,0)
  self.format_combo=Gtk.ComboBoxText()
  for title in FILM_FORMATS:self.format_combo.append_text(title)
  self.format_combo.set_active(self.s['film_format']);self.format_combo.set_hexpand(True)
  self.format_combo.set_tooltip_text('Maps the image long edge to the film frame. Smaller formats give coarser grain. Output pixels preserves the previous size convention.')
  self.format_combo.connect('changed',self._format_changed);format_row.pack_start(self.format_combo,True,True,0)
  notebook=Gtk.Notebook();advanced_box.pack_start(notebook,False,False,0)
  for zone,title in (('shadow','Shadows'),('mid','Midtones'),('high','Highlights')):
   section=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5);section.set_border_width(6)
   notebook.append_page(section,Gtk.Label(label=title))
   self._selector(section,zone+'_selection',True)
   self._pad(section,zone+'_')
   self._character_controls(section,zone)
  notebook.connect('switch-page',self._zone_sample_tone)
  self.control_tabs.set_current_page(1)
  self.control_tabs.connect('switch-page',self._control_page)
  self._select_pad('shadow_')
  self._tonal_controls(advanced_box)
  row=Gtk.Box(spacing=8);grey_box.pack_start(row,False,False,5)
  self.preview_check=Gtk.CheckButton(label='Full-canvas preview')
  self.preview_check.set_tooltip_text('Preview the combined Shadows, Midtones and Highlights settings on the GIMP canvas; the selected Advanced tab controls only its tonal zone. Make an image selection before opening for a faster preview. Hold B to compare before.')
  self.preview_check.set_active(False)
  self.preview_check.connect('toggled',self._toggle_preview)
  row.pack_start(self.preview_check,False,False,0)
  self.inspect_check=Gtk.CheckButton(label='View zone mask')
  self.inspect_check.set_tooltip_text('Show the selected tonal zone on the GIMP canvas: white = 100% coverage, black = 0%, gray = overlap. Coverage is independent of Amount, Size and grain profile; this diagnostic is never applied.')
  self.inspect_check.set_sensitive(False)
  self.inspect_check.connect('toggled',self._inspection_changed)
  row.pack_start(self.inspect_check,False,False,0)
  check=Gtk.Button(label='Check Installation…');check.connect('clicked',lambda *_:_check(self))
  row.pack_start(check,False,False,0)
  self.status=Gtk.Label(label='Preparing 100% grain sample…');self.status.set_xalign(0);self.status.set_ellipsize(Pango.EllipsizeMode.END)
  self.preview_busy=False
  self.preview_cursor_restore={};self.preview_wait_cursor=None
  self.connect('map',lambda *_:self._preview_cursor())
  self.preview_spinner=Gtk.Spinner();self.preview_spinner.set_size_request(20,20)
  self.preview_spinner.set_tooltip_text('Spinning: preview is updating. Crop ready: the latest settings have been displayed.')
  status_row=Gtk.Box(spacing=8);status_row.pack_start(self.preview_spinner,False,False,0)
  status_row.pack_start(self.status,True,True,0);root.pack_start(status_row,False,False,0)
  self.progress=Gtk.ProgressBar();self.progress.set_show_text(True);self.progress.set_text('Ready')
  self.progress.set_size_request(-1,26)
  self.progress.set_tooltip_text('Preview rendering and Apply progress. The bright filled section shows completion.')
  root.pack_start(self.progress,False,False,0)
  self.show_all();self.control_tabs.set_current_page(1);self._schedule()

 def _layer_controls(self,parent):
  row=Gtk.Box(spacing=6)
  parent.pack_start(row,False,False,0)
  label=Gtk.Label(label='Target layer')
  label.set_tooltip_text('Select a raster layer to receive a new grain result. The selected source is never modified. You can apply to several layers without closing this window.')
  row.pack_start(label,False,False,0)
  self.layer_combo=Gtk.ComboBoxText()
  self.layer_combo.set_hexpand(True)
  self.layer_combo.set_tooltip_text('Choose the source layer for the next Apply. This list includes raster layers, not layer groups. New results appear above their sources and do not replace them.')
  row.pack_start(self.layer_combo,True,True,0)
  refresh=Gtk.Button(label='↻')
  refresh.set_tooltip_text('Refresh layer choices if the image layers have changed. Does not apply grain.')
  refresh.connect('clicked',self._refresh_layers)
  row.pack_start(refresh,False,False,0)
  self.layer_combo.connect('changed',self._target_changed)
  self._refresh_layers()

 def _refresh_layers(self,*_):
  self.target_busy=True
  try:
   self.layer_options={}
   self.layer_combo.remove_all()
   def visit(layers,depth):
    for layer in layers:
     if layer == self.preview:continue
     if layer.is_group():
      visit(layer.get_children(),depth+1)
     elif _can_render_layer(layer):
      identifier=str(layer.get_id())
      self.layer_options[identifier]=layer
      self.layer_combo.append(identifier,'  '*depth+layer.get_name())
   visit(self.image.get_layers(),0)
   current=str(self.source.get_id())
   if current in self.layer_options:self.layer_combo.set_active_id(current)
   elif self.layer_options:self.layer_combo.set_active(0)
  finally:self.target_busy=False
  if str(self.source.get_id()) not in self.layer_options and self.layer_combo.get_active_id():
   self._target_changed(self.layer_combo)

 def _target_changed(self,combo):
  if self.target_busy or self.closed:return
  target=self.layer_options.get(combo.get_active_id())
  if target is None or target == self.source:return
  try:
   self._pause_preview()
   self.source=target
   self.editing=None
   self.display_base=target
   self.original_visibility=[(target,target.get_visible())]
   self.s['_frame_pixels']=max(target.get_width(),target.get_height())
   self.sample_requests.clear()
   self.sample_images.clear()
   self.sample_cache.clear()
   for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
   self._preview_status('Target: '+target.get_name())
   if self.live:self._schedule()
  except Exception as exc:
   self._preview_status('Layer change failed: '+str(exc))

 def _pause_preview(self):
  self.render_generation+=1
  self.cancel_render.set();self.jobs.clear()
  for future in self.pending:future.cancel()
  self.pending.clear()
  if self.timer is not None:
   GLib.source_remove(self.timer);self.timer=None
  if self.poll_id is not None:
   GLib.source_remove(self.poll_id);self.poll_id=None
  if self.preview is not None or self.preview_frozen:self._remove_preview()

 def _start_apply(self):
  self._pause_preview()
  self.set_deletable(False)
  self.columns.set_sensitive(False)
  for button in self.action_buttons:button.set_sensitive(False)
  self._apply_progress(0)

 def _finish_apply(self,message):
  self.columns.set_sensitive(True)
  for button in self.action_buttons:button.set_sensitive(True)
  self.set_deletable(True)
  self._refresh_layers()
  if self.live:self._schedule()
  else:self._preview_status(message)

 def _character_controls(self,parent,zone):
  self._heading(parent,'GRAIN CHARACTER — '+{'shadow':'SHADOWS','mid':'MIDTONES','high':'HIGHLIGHTS'}[zone])
  for control,title,detail in (
   ('colour_grain','Colour grain','Colour-film chromatic speckling. 0 is monochrome; 100 is maximum colour. The selected zone must use a colour-film profile.'),
   ('clumping','Clumping','Local clustering for this tonal zone only; 50 retains film default.'),
   ('fine_balance','Fine/coarse balance','Fine versus coarse grain structure for this tonal zone only; 50 retains film default.')):
   key=zone+'_'+control
   self._slider(parent,key,title)
   self.character_rows[key].set_tooltip_text(detail+' Double-click to restore 50.')
  note=Gtk.Label(label='Controls only this tonal zone · Main grain selects the inherited film profile')
  note.set_line_wrap(True);note.set_xalign(0)
  note.get_style_context().add_class('grain-guide');parent.pack_start(note,False,False,0)
  self._update_character_sensitivity()

 def _tonal_controls(self,parent):
  self._heading(parent,'TONAL COVERAGE')
  detail=Gtk.Label(label='Zones follow the pixel’s RGB brightness (black → white), not the subject. Bright-looking blue skies can fall in Shadows/Midtones; bright clouds and pale stone often fall in Highlights.')
  detail.set_line_wrap(True);detail.set_xalign(0)
  detail.get_style_context().add_class('grain-guide')
  parent.pack_start(detail,False,False,0)
  self.tonal_graph=Gtk.DrawingArea()
  self.tonal_graph.set_size_request(280,66)
  self.tonal_graph.set_tooltip_text('Exact tonal-zone weights: dark pixels on the left, bright pixels on the right. The active zone is the pale curve; other curves are gray. Weights have soft overlaps, not hard boundaries.')
  self.tonal_graph.connect('draw',self._draw_tonal_graph)
  parent.pack_start(self.tonal_graph,False,False,0)
  self._slider(parent,'shadow_crossover','Shadows ↔ Midtones')
  self._slider(parent,'highlight_crossover','Midtones ↔ Highlights')
  self.tonal_readout=Gtk.Label()
  self.tonal_readout.set_xalign(0)
  self.tonal_readout.set_line_wrap(True)
  self.tonal_readout.get_style_context().add_class('grain-guide')
  parent.pack_start(self.tonal_readout,False,False,0)
  self._update_tonal_guide()

 def _update_tonal_guide(self):
  if not hasattr(self,'tonal_readout'):return
  shadow=round(self.s['shadow_crossover'])
  highlight=round(self.s['highlight_crossover'])
  self.tonal_readout.set_text(
   f'50% shadow weight at {shadow}% brightness · 50% highlight weight at {highlight}%. '
   'Lower the Highlights crossover to include more sky/midtones.')
  self.tonal_graph.queue_draw()

 def _draw_tonal_graph(self,area,cr):
  width=max(1,area.get_allocated_width());height=max(1,area.get_allocated_height())
  left,right,top,bottom=9,width-9,23,height-7
  span=max(1,right-left)
  for x in range(left,right):
   shade=.13+.76*(x-left)/span
   cr.set_source_rgb(shade,shade,shade)
   cr.rectangle(x,4,1,13);cr.fill()
  tones=np.linspace(0,1,129,dtype=np.float32)
  weights=_weights(tones,self.s)
  selected=('shadow_','mid_','high_').index(self.zone_prefix)
  for k in (0,1,2):
   ycurve=weights[k]
   if selected==k:cr.set_source_rgb(.96,.96,.96);cr.set_line_width(2.4)
   else:cr.set_source_rgb(.46,.46,.46);cr.set_line_width(1.15)
   for j,value in enumerate(ycurve):
    x=left+span*j/128;y=bottom-(bottom-top)*value
    if j==0:cr.move_to(x,y)
    else:cr.line_to(x,y)
   cr.stroke()
  return False

 def _inspection_changed(self,button):
  if self.live:self._schedule()

 def _update_character_sensitivity(self):
  for zone in ('shadow','mid','high'):
   key=zone+'_colour_grain'
   if key not in self.character_rows:continue
   choice=int(self.s[zone+'_selection'])
   if choice<0:choice=int(self.s['selection'])
   self.character_rows[key].set_sensitive(choice in COLOUR_MODELS)

 def _heading(self,parent,title):
  l=Gtk.Label();l.set_markup('<b>'+title+'</b>');l.set_xalign(0);l.get_style_context().add_class('grain-heading')
  parent.pack_start(l,False,False,4)
 def _selector(self,parent,key,inherit):
  row=Gtk.Box(spacing=6);parent.pack_start(row,False,False,0)
  l=Gtk.Label(label='Grain');l.set_xalign(0);l.set_size_request(62,-1)
  row.pack_start(l,False,False,0)
  c=Gtk.MenuButton();c.set_hexpand(True);c.get_style_context().add_class('grain-picker')
  content=Gtk.Box(spacing=8)
  label=Gtk.Label(label='Use main grain · '+LABELS[self.s['selection']] if self.s[key]<0 else LABELS[self.s[key]])
  label.set_xalign(0);label.set_ellipsize(Pango.EllipsizeMode.END);label.set_hexpand(True)
  content.pack_start(label,True,True,0);content.pack_end(Gtk.Label(label='▾'),False,False,0)
  previous=c.get_child()
  if previous:c.remove(previous)
  c.add(content);row.pack_start(c,True,True,0)
  popover=Gtk.Popover.new(c);popover.set_position(Gtk.PositionType.BOTTOM)
  popover.set_constrain_to(Gtk.PopoverConstraint.WINDOW)
  popover.get_style_context().add_class('grain-popover')
  scroller=Gtk.ScrolledWindow();scroller.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
  scroller.set_size_request(300,self.menu_height)
  box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2);box.set_border_width(6)
  scroller.add(box);popover.add(scroller);c.set_popover(popover)
  groups=(('Measured scans',range(1,6)),
          ('Sensitometry-informed · Spektrafilm',range(18,23)),
          ('Manufacturer RMS · modelled texture',(15,23,24,25,26,27,28,29)),
          ('Artistic interpretations · unmeasured',(0,6,7,8,9,10,11,12,13,14,16,17)))
  choices={}
  def add_choice(index,name):
   button=Gtk.Button();text=Gtk.Label(label=name);text.set_xalign(0)
   text.set_ellipsize(Pango.EllipsizeMode.END);text.set_max_width_chars(37)
   button.add(text);button.set_tooltip_text(self._grain_tooltip(index))
   if index==self.s[key]:button.get_style_context().add_class('selected')
   button.connect('clicked',self._pick_grain,key,index,popover)
   box.pack_start(button,False,False,0);choices[index]=button
  if inherit:add_choice(-1,'Use main grain')
  for number,(title,indices) in enumerate(groups):
   if number or inherit:box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),False,False,0)
   heading=Gtk.Label(label=title);heading.set_xalign(0)
   heading.get_style_context().add_class('grain-heading');box.pack_start(heading,False,False,0)
   for index in indices:add_choice(index,LABELS[index])
  popover.show_all();popover.hide();popover.set_no_show_all(True)
  c.set_tooltip_text(self._grain_tooltip(self.s[key]))
  self.combos[key]=c;self.combo_labels[key]=label;self.choice_buttons[key]=choices
  if not inherit:
   dice=Gtk.Button(label='↻');dice.set_tooltip_text('Choose a new repeatable grain pattern.')
   dice.connect('clicked',self._randomize);row.pack_start(dice,False,False,0)
 def _grain_tooltip(self,index):
  if index<0:return 'Inherits the currently selected main grain: '+LABELS[self.s['selection']]+'. Changing the main film also changes inherited tonal zones.'
  spec=PROFILE_SPECS[index]
  basis={'Measured':'Scan-derived grain statistics and spatial noise spectrum.',
         'Sensitometry':'Published density-response proxy; grain texture is artist-modelled.',
         'RMS':'Manufacturer RMS information guides relative strength; texture is artist-modelled.',
         'Artistic':'Unmeasured artistic interpretation; not a physical film match.'}[spec['kind']]
  return LABELS[index]+' — '+basis+' Selects the grain recipe without applying it to the image.'
 def _pick_grain(self,button,key,index,popover):
  self.s[key]=index;self.s['engine_version']=4;self._update_selection(key);popover.popdown();self.combos[key].set_active(False)
  for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
  self._update_character_sensitivity()
  if key=='selection':
   for z in ('shadow','mid','high'):self._update_selection(z+'_selection')
  self._schedule()
 def _update_selection(self,key):
  selected=self.s[key]
  self.combo_labels[key].set_text('Use main grain · '+LABELS[self.s['selection']] if selected<0 else LABELS[selected])
  self.combos[key].set_tooltip_text(self._grain_tooltip(selected))
  for index,button in self.choice_buttons[key].items():
   context=button.get_style_context()
   if index==selected:context.add_class('selected')
   else:context.remove_class('selected')
 def _format_changed(self,combo):
  if self.busy:return
  self.s['film_format']=combo.get_active()
  self.s['engine_version']=4
  for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
  self._schedule()
 def _pad(self,parent,prefix):
  area=Gtk.DrawingArea();area.set_size_request(280,240);area.set_hexpand(True);area.set_vexpand(True)
  area.set_tooltip_text('This is a sample of the active grain on a flat reference tone, not a map of the photograph. Drag left/right for Size and up/down for Amount. Use View zone mask to see tonal coverage on the canvas.')
  area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON1_MOTION_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK)
  area.connect('draw',self._draw_pad,prefix)
  area.connect('button-press-event',self._move_pad,prefix)
  area.connect('motion-notify-event',self._move_pad,prefix)
  area.connect('button-release-event',self._release_pad)
  self.grain_stack.add_named(area,prefix or 'main');self.pads[prefix]=area;self.pad_zooms[prefix]=1
  row=Gtk.Box(spacing=8);parent.pack_start(row,False,False,0)
  zoom_group=None
  for zoom in (1,2):
   button=Gtk.RadioButton.new_with_label_from_widget(zoom_group,f'{zoom*100}%')
   if zoom_group is None:zoom_group=button
   button.set_mode(False)
   button.set_tooltip_text(f'Inspect the grain texture at {zoom*100}% enlargement. Switch freely between 100% and 200%; output is unaffected.')
   button.connect('toggled',self._zoom_pad,prefix,zoom)
   row.pack_start(button,False,False,0)
   self.pads[(prefix,zoom)]=button
  self.pads[(prefix,1)].set_active(True)
  softness_row=Gtk.Box(spacing=5)
  parent.pack_start(softness_row,False,False,0)
  self._slider(softness_row,prefix+'softness','Softness',minimum_width=215)
  read=Gtk.Label();read.set_xalign(0);read.get_style_context().add_class('grain-readout');parent.pack_start(read,False,False,0)
  self.readouts[prefix]=read
  for control in ('amount','size'):
   key=prefix+control;lo,hi,step=FLOAT_RANGES[key]
   a=Gtk.Adjustment.new(self.s[key],lo,hi,step,step*10,0)
   a.connect('value-changed',self._changed,key);self.controls[key]=a
  self._update_pad(prefix)
 def _update_pad(self,prefix):
  if prefix in self.readouts:
   self.readouts[prefix].set_text('Amount %d    Size %.2f'%(round(self.s[prefix+'amount']),self.s[prefix+'size']))
  if prefix in self.pads:self.pads[prefix].queue_draw()
 def _zoom_pad(self,button,prefix,zoom):
  if not button.get_active():return
  self.pad_zooms[prefix]=zoom
  self._update_pad(prefix)
 def _release_pad(self,area,event):
  self.dragging=False;self.pad_action=None;self._schedule()
  return True
 def _move_pad(self,area,event,prefix):
  a=area.get_allocation();margin=18
  if event.type==Gdk.EventType.BUTTON_PRESS:
   if event.button!=1:return False
   self.dragging=True;self.pad_action='dot'
  if event.type==Gdk.EventType.MOTION_NOTIFY and not (event.state & Gdk.ModifierType.BUTTON1_MASK):return False
  fx=max(0,min(1,(event.x-margin)/max(1,a.width-2*margin)))
  fy=max(0,min(1,(event.y-margin)/max(1,a.height-2*margin)))
  self.controls[prefix+'size'].set_value(.5+1.5*fx)
  self.controls[prefix+'amount'].set_value(100*(1-fy))
  return True
 def _grey_preview(self,parent):
  self.panel_title=Gtk.Label(label='SHADOWS GRAIN');self.panel_title.set_xalign(0)
  self.panel_title.get_style_context().add_class('grain-heading');parent.pack_start(self.panel_title,False,False,0)
  self.grain_stack=Gtk.Stack();self.grain_stack.set_transition_type(Gtk.StackTransitionType.NONE)
  parent.pack_start(self.grain_stack,True,True,0)
  row=Gtk.Box(spacing=8);parent.pack_start(row,False,False,0)
  before=Gtk.Button(label='Hold Before')
  before.set_tooltip_text('Hold to compare the image or grain panel before applying the selected grain. Release to restore the current preview.')
  before.connect('pressed',self._before_grey,True)
  before.connect('released',self._before_grey,False)
  before.connect('grab-broken-event',self._before_grey,False)
  row.pack_start(before,False,False,0)
  row.pack_start(Gtk.Label(label='B = Before'),False,False,0)
  guide=Gtk.Label(label='Flat-tone sample · Drag dot: left/right Size · up/down Amount');guide.set_xalign(0)
  guide.get_style_context().add_class('grain-guide');parent.pack_start(guide,False,False,0)
 def _before_grey(self,button,*args):
  self.before_mouse=bool(args[-1]);self._sync_before();return False
 def _sync_before(self):
  previous=self.show_before;self.show_before=self.before_mouse or self.before_key
  self._update_pad(self.active_pad)
  if previous!=self.show_before and self.live and self.preview is not None:
   try:self._show_preview(not self.show_before);Gimp.displays_flush()
   except Exception as exc:self._preview_status('Before comparison error: '+str(exc))
 def _before_key_event(self,window,event,pressed):
  if Gdk.keyval_to_lower(event.keyval)!=Gdk.KEY_b:return False
  if pressed and event.state & (Gdk.ModifierType.CONTROL_MASK|Gdk.ModifierType.MOD1_MASK|Gdk.ModifierType.SUPER_MASK):return False
  self.before_key=pressed;self._sync_before();return True
 def _before_focus_out(self,*args):
  self.before_key=False;self.before_mouse=False;self._sync_before();return False
 def _select_pad(self,prefix):
  self.active_pad=prefix;self.grain_stack.set_visible_child_name(prefix or 'main')
  self.panel_title.set_text({'':'MAIN GRAIN','shadow_':'SHADOWS GRAIN','mid_':'MIDTONES GRAIN','high_':'HIGHLIGHTS GRAIN'}[prefix])
  self._update_pad(prefix)
 def _control_page(self,notebook,page,index):
  self._select_pad('' if index==0 else self.zone_prefix)
  if hasattr(self,'inspect_check'):
   self.inspect_check.set_sensitive(self.live and index==1)
   if index==0 and self.inspect_check.get_active():self.inspect_check.set_active(False)
 def _zone_sample_tone(self,notebook,page,index):
  self.zone_prefix=('shadow_','mid_','high_')[index]
  if self.control_tabs.get_current_page()==1:self._select_pad(self.zone_prefix)
  self._update_tonal_guide()
  if hasattr(self,'inspect_check') and self.inspect_check.get_active():self._schedule()
 def _draw_pad(self,area,cr,prefix):
  a=area.get_allocation();w,h=a.width,a.height
  tone={'shadow_':.18,'mid_':.50,'high_':.82}.get(prefix,.50)
  cr.set_source_rgb(tone,tone,tone);cr.rectangle(0,0,w,h);cr.fill()
  zoom=self.pad_zooms[prefix];sw=max(1,math.ceil(w/zoom));sh=max(1,math.ceil(h/zoom))
  settings=self._snapshot()
  if prefix:
   for zone in ('shadow','mid','high'):
    if zone+'_'!=prefix:settings[zone+'_amount']=0
  image_key=(w,h,zoom,tuple(sorted(settings.items())))
  cached=self.sample_images.get(prefix)
  if cached is not None and not self.show_before:
   pixbuf=cached[1]
   dx=0;dy=0
   Gdk.cairo_set_source_pixbuf(cr,pixbuf,dx,dy);cr.paint()
  if not self.closed and (cached is None or cached[0]!=image_key):
   previous=self.sample_requests.get(prefix)
   if previous is None or previous[0]!=image_key:
    self.sample_due[prefix]=time.monotonic()+.12
    if self.sample_pending is not None and (self.sample_pending[1]!=prefix or self.sample_pending[2]!=image_key):self.sample_cancel.set()
    if not self.live:self._preview_status(f'Updating {zoom*100}% grain panel…',True)
   self.sample_requests[prefix]=(image_key,sw,sh,zoom,settings)
   if self.sample_poll_id is None:self.sample_poll_id=GLib.timeout_add(30,self._sample_poll)
  cr.set_source_rgba(.6,.6,.6,.18);cr.rectangle(w/2,0,.7,h);cr.fill()
  cr.rectangle(0,h/2,w,.7);cr.fill()
  cr.set_source_rgba(.06,.06,.06,.82);cr.rectangle(5,4,76,21);cr.fill()
  cr.rectangle(max(5,w-57),h-26,52,21);cr.fill()
  cr.set_source_rgb(.95,.95,.95);cr.select_font_face('Sans',0,1);cr.set_font_size(12)
  cr.move_to(10,19);cr.show_text('AMOUNT')
  cr.move_to(max(10,w-50),h-11);cr.show_text('SIZE')
  cr.set_source_rgba(.7,.7,.7,.9);cr.rectangle(1,1,w-2,h-2);cr.set_line_width(1.5);cr.stroke()
  margin=18;x=margin+(w-2*margin)*(self.s[prefix+'size']-.5)/1.5
  y=margin+(h-2*margin)*(1-self.s[prefix+'amount']/100)
  cr.set_source_rgba(.7,.7,.7,.2);cr.arc(x,y,16,0,2*math.pi);cr.fill()
  cr.set_source_rgba(.7,.7,.7,.3);cr.arc(x,y,12,0,2*math.pi);cr.fill()
  cr.set_source_rgb(.72,.72,.72);cr.arc(x,y,11,0,2*math.pi);cr.fill_preserve()
  cr.set_source_rgb(.96,.96,.96);cr.set_line_width(2);cr.stroke()
  return False
 def _sample_poll(self):
  if self.closed:self.sample_poll_id=None;return False
  if self.sample_pending is not None:
   future,prefix,key,w,h,zoom=self.sample_pending
   if future.done():
    self.sample_pending=None
    request=self.sample_requests.get(prefix)
    if request is not None and request[0]==key:
     try:
      rgb=future.result()
      pixbuf=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb),GdkPixbuf.Colorspace.RGB,False,8,w,h,w*3)
      if zoom!=1:pixbuf=pixbuf.scale_simple(w*zoom,h*zoom,GdkPixbuf.InterpType.NEAREST)
      self.sample_images[prefix]=(key,pixbuf);del self.sample_requests[prefix]
      self.pads[prefix].queue_draw()
      if not self.live:self._preview_status(f'{zoom*100}% grain panel ready')
     except CancelledError:pass
     except Exception as exc:
      self.sample_requests.pop(prefix,None)
      if not self.live:self._preview_status('Grain sample error: '+str(exc))
      else:self.status.set_text('Grain sample error: '+str(exc))
  if self.sample_pending is None:
   visible=[prefix for prefix in self.sample_requests if self.pads[prefix].get_mapped() and time.monotonic()>=self.sample_due.get(prefix,0)]
   if visible:
    prefix=next((p for p in visible if p!=self.sample_turn),visible[0])
    key,w,h,zoom,settings=self.sample_requests[prefix]
    tone={'shadow_':.18,'mid_':.50,'high_':.82}.get(prefix,.50)
    self.sample_cancel=threading.Event()
    future=self.sample_pool.submit(_sample_tile,w,h,tone,settings,self.sample_cache,self.sample_cancel)
    self.sample_pending=(future,prefix,key,w,h,zoom);self.sample_turn=prefix
   else:
    self.sample_requests={p:r for p,r in self.sample_requests.items() if self.pads[p].get_mapped()}
  if self.sample_pending is not None or self.sample_requests:return True
  self.sample_poll_id=None;return False
 def _slider(self,parent,key,label,minimum_width=65):
  lo,hi,step=FLOAT_RANGES[key];row=Gtk.Box(spacing=5);parent.pack_start(row,True,True,0)
  if key.endswith(('_colour_grain','_clumping','_fine_balance')):self.character_rows[key]=row
  l=Gtk.Label(label=label);l.set_xalign(0);l.set_size_request(62,-1)
  row.pack_start(l,False,False,0)
  a=Gtk.Adjustment.new(self.s[key],lo,hi,step,step*10,0)
  sc=Gtk.Scale.new(Gtk.Orientation.HORIZONTAL,a);sc.set_draw_value(False);sc.set_hexpand(True);sc.set_size_request(minimum_width,-1)
  descriptions={
   'amount':'Overall strength of all grain zones.',
   'size':'Main grain magnification; multiplies each zone size.',
   'softness':'Main grain softness; adds to each zone softness.',
   'colour_grain':'Chromatic speckling on colour-film profiles. At 0, grain is monochrome; at 50, colour variation is half the maximum; at 100, it reaches full strength. No effect on B&W profiles.',
   'clumping':'Controls local grain clustering. 50 is the emulation default; higher settings form stronger clusters.',
   'fine_balance':'Shifts between fine and coarse grain structures. 50 is the emulation default.',
   'shadow_crossover':'The pixel brightness where the Shadows mask has 50% strength. Raise it to give shadow grain a wider tonal reach. Smooth blends avoid visible steps; this is not a hard boundary.',
   'highlight_crossover':'The pixel brightness where the Highlights mask has 50% strength. Lower it to give highlight grain to darker colours, including blue skies. Smooth blends avoid hard boundaries.'}
  for prefix,title in (('shadow_','shadow'),('mid_','midtone'),('high_','highlight')):
   descriptions[prefix+'softness']='Softness of the '+title+' grain; combines with main softness.'
   descriptions[prefix+'amount']='Strength of '+title+' grain; combines with overall Amount.'
   descriptions[prefix+'size']='Size of '+title+' grain; combines with main Size.'
  if key not in descriptions and key.endswith(('_colour_grain','_clumping','_fine_balance')):
   zone,control=key.split('_',1)
   descriptions[key]={'colour_grain':'Colour speckle for this tonal zone only, linear from zero to full strength; requires colour film.','clumping':'Grain clustering for this tonal zone only.','fine_balance':'Fine/coarse grain mixture for this tonal zone only.'}[control]
  tooltip=descriptions[key]+' Double-click the slider to restore its default.'
  sc.set_tooltip_text(tooltip);l.set_tooltip_text(tooltip)
  sc.connect('button-press-event',self._reset_double,key)
  row.pack_start(sc,True,True,0)
  spin=Gtk.SpinButton.new(a,step,2 if step<1 else 0);spin.set_width_chars(4)
  spin.set_tooltip_text(tooltip)
  self.spins.append(spin)
  row.pack_start(spin,False,False,0)
  a.connect('value-changed',self._changed,key);self.controls[key]=a
 def _reset_double(self,scale,event,key):
  if event.type==Gdk.EventType._2BUTTON_PRESS:
   self.controls[key].set_value(DEFAULT[key]);return True
  return False
 def _changed(self,a,key):
  if self.busy:return
  self.s[key]=a.get_value()
  self.s['engine_version']=4
  self._update_character_sensitivity()
  self._update_tonal_guide()
  for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
  self._schedule()
 def _snapshot(self,commit=False):
  if commit:
   for spin in self.spins:spin.update()
  values=self.s.copy()
  for key,adjustment in self.controls.items():values[key]=adjustment.get_value()
  values['film_format']=self.format_combo.get_active()
  settings=settings_from(values);settings['_frame_pixels']=self.s['_frame_pixels']
  return settings
 def _randomize(self,*_):
  self.s['seed']=int.from_bytes(os.urandom(4),'little')&0x7fffffff
  for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
  self._schedule()
 def _response(self,dialog,response):
  if response!=1:return
  self.busy=True;frame_pixels=self.s['_frame_pixels'];self.s=DEFAULT.copy();self.s['_frame_pixels']=frame_pixels
  self.format_combo.set_active(self.s['film_format'])
  for k,a in self.controls.items():a.set_value(self.s[k])
  for key in self.combos:self._update_selection(key)
  self._update_character_sensitivity()
  self._update_tonal_guide()
  for prefix in ('','shadow_','mid_','high_'):self._update_pad(prefix)
  self.busy=False;self._schedule();self.stop_emission_by_name('response')
 def _preview_cursor(self):
  if not self.preview_busy:
   for window,cursor in self.preview_cursor_restore.items():
    if not window.is_destroyed():window.set_cursor(cursor)
   self.preview_cursor_restore.clear()
  else:
   root=self.get_window()
   if root is None:return
   if self.preview_wait_cursor is None:
    display=root.get_display()
    self.preview_wait_cursor=Gdk.Cursor.new_from_name(display,'wait')
    if self.preview_wait_cursor is None:
     self.preview_wait_cursor=Gdk.Cursor.new_for_display(display,Gdk.CursorType.WATCH)
   windows=[root]
   while windows:
    window=windows.pop()
    if window.is_destroyed():continue
    if window not in self.preview_cursor_restore:
     self.preview_cursor_restore[window]=window.get_cursor()
    window.set_cursor(self.preview_wait_cursor)
    windows.extend(window.get_children())
  self.get_display().flush()
 def _settings_label(self,settings):
  return ('S %d · M %d · H %d' %
          tuple(round(settings[z+'_amount']) for z in ('shadow','mid','high')))
 def _preview_status(self,text,busy=False):
  if busy!=self.preview_busy:
   self.preview_busy=busy
   if busy:self.preview_spinner.start()
   else:self.preview_spinner.stop()
   self._preview_cursor()
  self.status.set_text(text)
  self.progress.set_text(text)
  if busy:
   if text.startswith('Canvas preview '):self.progress.set_fraction(self.preview_done/max(1,self.preview_total))
   elif text.startswith('Updating canvas') or text.startswith('Preview queued'):self.progress.set_fraction(0)
   else:self.progress.pulse()
  else:self.progress.set_fraction(1 if 'ready' in text.lower() else 0)
 def _apply_progress(self,fraction):
  self.progress.set_fraction(fraction);self.progress.set_text(f'Applying grain {round(fraction*100)}%')
  self.status.set_text('Applying to a new layer…')
  context=GLib.MainContext.default()
  for _ in range(12):
   if not context.pending():break
   context.iteration(False)
 def _toggle_preview(self,button):
  self.live=button.get_active()
  self.inspect_check.set_sensitive(self.live and self.control_tabs.get_current_page()==1)
  if not self.live and self.inspect_check.get_active():self.inspect_check.set_active(False)
  if self.preview is not None:
   try:self._show_preview(self.live);Gimp.displays_flush()
   except Exception as exc:
    self.live=False;self.render_generation+=1;self.cancel_render.set();self.jobs.clear()
    self._preview_status('Preview error: '+str(exc))
    self._remove_preview();button.set_active(False)
    return
  if self.live:self._schedule()
  else:
   self.render_generation+=1;self.cancel_render.set();self.jobs.clear()
   for future in self.pending:future.cancel()
   if self.timer:GLib.source_remove(self.timer);self.timer=None
   self._preview_status('Grey sample active — full canvas off');self._update_pad('')
 def _show_preview(self,visible):
  if self.preview is None:return
  if not self.preview.set_visible(visible):raise RuntimeError('Could not change preview visibility.')
  for layer,original in self.original_visibility:
   if layer==self.source:continue
   if not layer.set_visible(False if visible else original):
    raise RuntimeError('Could not change layer visibility. Check its visibility lock.')
 def _remove_preview(self):
  try:
   if self.preview is not None:
    if not self.image.remove_layer(self.preview):
     raise RuntimeError('Could not remove temporary grain preview.')
    self.preview=None
  finally:
   try:
    for layer,original in self.original_visibility:layer.set_visible(original)
   finally:
    if self.preview_frozen:
     self.image.undo_thaw();self.preview_frozen=False
    self.srcbuf=None;self.preview_buffer=None;self.preview_dirty.clear()
 def _schedule(self):
  if self.closed:return
  self._update_pad(self.active_pad)
  if not self.live:return
  self.render_generation+=1;self.cancel_render.set()
  self.cancel_render=threading.Event();self.jobs.clear()
  self.preview_dirty.clear()
  for future in self.pending:future.cancel()
  if self.timer:GLib.source_remove(self.timer)
  self._preview_status('Preview queued…',True)
  self.timer=GLib.timeout_add(120,self._begin_preview)
 def _begin_preview(self):
  self.timer=None
  if self.closed or not self.live or np is None:return False
  try:
   if self.preview is None:
    if not self.image.undo_freeze():raise RuntimeError('Could not freeze undo during preview.')
    self.preview_frozen=True
    # Preview the original source with the new settings, never the old grain result.
    self.preview=self.source.copy();self.preview.set_name('Film Grain Preview')
    if not self.image.insert_layer(self.preview,self.source.get_parent(),0):
     self.preview=None;raise RuntimeError('Could not create preview layer.')
    _copy_attributes(self.source,self.preview)
    ok,ox,oy=self.source.get_offsets()
    if ok:self.preview.set_offsets(ox,oy)
    self.srcbuf=self.source.get_buffer();self.preview_buffer=self.preview.get_buffer()
    self._show_preview(not self.show_before)
   w=self.source.get_width();h=self.source.get_height()
   self.preview_settings=self._snapshot()
   if self.inspect_check.get_active() and self.control_tabs.get_current_page()==1:
    self.preview_settings['_inspect_zone']=('shadow_','mid_','high_').index(self.zone_prefix)
   self.preview_settings_revision=self.render_generation
   self.srcbuf=self.source.get_buffer();self.preview_buffer=self.preview.get_buffer()
   self.selection_buffer=None if Gimp.Selection.is_empty(self.image) else self.image.get_selection().get_buffer()
   _,self.selection_x,self.selection_y=self.source.get_offsets()
   bounds=None
   if self.selection_buffer is not None:
    ok,nonempty,x0,y0,x1,y1=Gimp.Selection.bounds(self.image)
    if ok:bounds=(x0-self.selection_x,y0-self.selection_y,x1-self.selection_x,y1-self.selection_y) if nonempty else (0,0,0,0)
   tiles=_tiles(w,h,bounds)
   tiles.sort(key=lambda tile:(tile[0]+tile[2]/2-w/2)**2+(tile[1]+tile[3]/2-h/2)**2)
   self.jobs=deque(tiles);self.preview_done=0;self.preview_total=len(tiles)
   self._preview_status('Updating canvas preview…',True)
   if not tiles:self._preview_status('No selected pixels on this layer')
   if self.poll_id is None:self.poll_id=GLib.timeout_add(12,self._preview_poll)
  except Exception as exc:
   self.render_generation+=1;self.cancel_render.set();self.jobs.clear()
   self._preview_status('Preview error: '+str(exc));self._remove_preview()
  return False
 def _preview_poll(self):
  if self.closed or not self.live:self.poll_id=None;return False
  if self.timer is not None:return True
  try:
   deadline=time.monotonic()+.008
   for future,info in list(self.pending.items()):
    if not future.done():continue
    del self.pending[future]
    gen,x,y,w,h=info
    if gen!=self.render_generation or self.preview is None:continue
    try:pixels=future.result()
    except CancelledError:continue
    write_pixels(self.preview_buffer,x,y,pixels);self.preview_dirty.append((x,y,w,h))
    self.preview_done+=1
    if time.monotonic()>=deadline:break
   if self.preview_dirty and (time.monotonic()-self.last_publish>=.12 or self.preview_done==self.preview_total):
    # Only the disposable preview buffer is written; no repeated shadow merges.
    self.preview_buffer.flush()
    for region in self.preview_dirty:self.preview.update(*region)
    self.preview_dirty.clear();self.last_publish=time.monotonic()
    Gimp.displays_flush()
    percent=min(100,100*self.preview_done//max(1,self.preview_total))
    label=('Tonal mask ready · '+('Shadows','Midtones','Highlights')[self.preview_settings['_inspect_zone']]+' (white = full coverage, not grain strength)' if '_inspect_zone' in self.preview_settings else 'Preview ready · '+self._settings_label(self.preview_settings))
    self._preview_status(label if percent==100 else f'Canvas preview {percent}%',percent<100)
   while self.jobs and len(self.pending)<self.worker_count and time.monotonic()<deadline:
    x,y,w,h=self.jobs.popleft()
    pixels=read_pixels(self.srcbuf,x,y,w,h)
    selection=None
    if self.selection_buffer is not None:
     selection=read_selection(self.selection_buffer,x+self.selection_x,y+self.selection_y,w,h)
    future=self.pool.submit(_render_preview_tile,pixels,x,y,self.preview_settings,self.cache,self.cancel_render,selection)
    self.pending[future]=(self.render_generation,x,y,w,h)
  except Exception as exc:
   self.render_generation+=1;self.cancel_render.set();self.jobs.clear()
   self._preview_status('Preview error: '+str(exc))
   self._remove_preview()
  if self.pending or self.jobs:return True
  self.poll_id=None;return False
 def cleanup(self):
  self._preview_status('Preview stopped')
  self.closed=True;self.render_generation+=1;self.cancel_render.set();self.jobs.clear()
  self.sample_cancel.set();self.sample_requests.clear()
  if self.sample_poll_id:GLib.source_remove(self.sample_poll_id);self.sample_poll_id=None
  if self.sample_pending is not None:self.sample_pending[0].cancel()
  self.sample_pool.shutdown(wait=False,cancel_futures=True);self.sample_pending=None
  if self.timer:GLib.source_remove(self.timer);self.timer=None
  if self.poll_id:GLib.source_remove(self.poll_id);self.poll_id=None
  for future in self.pending:future.cancel()
  self.pool.shutdown(wait=False,cancel_futures=True);self.pending.clear()
  self.srcbuf=None;self.preview_buffer=None;self.sample_images.clear();self.sample_cache.clear()
  self.selection_buffer=None
  if self.preview or self.preview_frozen:self._remove_preview()
  Gimp.displays_flush()


def run(procedure,run_mode,image,drawables,config,data):
 try:return _run(procedure,run_mode,image,drawables,config,data)
 except Exception as exc:
  message=str(exc)+'\n\n'+traceback.format_exc()
  if run_mode==Gimp.RunMode.INTERACTIVE:_notice(None,'Analogue Grain: error',message)
  return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,GLib.Error(message))


def _apply_once(image,source,settings,config,render_cache=None,dialog=None):
 settings=settings.copy()
 settings['_frame_pixels']=max(source.get_width(),source.get_height())
 target=None;started=False
 try:
  Gimp.progress_init('Rendering Analogue Grain…')
  if not image.undo_group_start():
   raise RuntimeError('Could not start an undo group.')
  started=True
  target=source.copy()
  names=set()
  def collect(layers):
   for layer in layers:
    names.add(layer.get_name())
    if layer.is_group():collect(layer.get_children())
  collect(image.get_layers())
  name=NAME;number=1
  while name in names:name=f'{NAME} #{number}';number+=1
  target.set_name(name)
  if not image.insert_layer(target,source.get_parent(),0):
   raise RuntimeError('Could not insert output layer.')
  ok,ox,oy=source.get_offsets()
  if ok:target.set_offsets(ox,oy)
  _copy_attributes(source,target)
  w=source.get_width();h=source.get_height()
  src=source.get_buffer();dst=target.get_shadow_buffer()
  selection_buffer=None if Gimp.Selection.is_empty(image) else image.get_selection().get_buffer()
  _,offset_x,offset_y=source.get_offsets()
  mask_reader=(lambda x,y,w,h:read_selection(selection_buffer,x+offset_x,y+offset_y,w,h)) if selection_buffer is not None else None
  if render_cache is None:render_cache=RenderCache()
  def progress(fraction):
   Gimp.progress_update(fraction)
   if dialog is not None:dialog._apply_progress(fraction)
  _render_jobs(_tiles(w,h),settings,
               lambda x,y,w,h:read_pixels(src,x,y,w,h),
               lambda x,y,pixels:write_pixels(dst,x,y,pixels),
               render_cache,progress,selection=mask_reader)
  dst.flush()
  if not target.merge_shadow(True):raise RuntimeError('Could not merge grain output.')
  target.update(0,0,w,h)
  _attach(target,source,settings)
  _store_config(config,settings)
  if not target.set_visible(True):
   raise RuntimeError('Could not show output layer. Check its visibility lock.')
  if not image.set_selected_layers([target]):
   raise RuntimeError('Could not select the new grain layer.')
  ended=image.undo_group_end()
  started=False
  if not ended:raise RuntimeError('Could not finish the grain undo group.')
  Gimp.displays_flush()
  return target
 except Exception:
  if target is not None:
   try:
    if target.is_valid():image.remove_layer(target)
   except Exception:pass
  raise
 finally:
  if started:image.undo_group_end()


def _run(procedure,run_mode,image,drawables,config,data):
 if len(drawables)!=1 or not _can_render_layer(drawables[0]):
  return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,GLib.Error('Select exactly one raster layer.'))
 if image.get_base_type() not in (Gimp.ImageBaseType.RGB,Gimp.ImageBaseType.GRAY):
  return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,GLib.Error('Requires RGB or grayscale image.'))
 Gegl.init(None)
 if run_mode==Gimp.RunMode.INTERACTIVE:GimpUi.init('analogue-grain')
 if np is None:
  if run_mode==Gimp.RunMode.INTERACTIVE:_check(None)
  return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,GLib.Error('NumPy unavailable: '+str(NUMPY_ERROR)))
 selected=drawables[0]
 old=_recipe(selected)
 editing=selected if old else None
 source=image.get_layer_by_tattoo(old['source_tattoo']) if old else selected
 if source is None or source==editing:
  return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,GLib.Error('Original source layer was removed.'))
 if old and (source.get_width(),source.get_height())!=(editing.get_width(),editing.get_height()):
  return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,GLib.Error('Source size changed; choose source instead.'))
 settings=settings_from(old.get('settings')) if old else _config_settings(config)
 if run_mode in (Gimp.RunMode.NONINTERACTIVE,Gimp.RunMode.WITH_LAST_VALS):
  try:_apply_once(image,source,settings,config)
  except Exception as exc:return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,GLib.Error(str(exc)))
  return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS,None)
 if run_mode!=Gimp.RunMode.INTERACTIVE:
  return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,GLib.Error('Unsupported run mode.'))
 dialog=None;applied=0
 try:
  dialog=GrainDialog(image,source,settings,editing)
  while True:
   response=dialog.run()
   if response!=Gtk.ResponseType.APPLY:break
   settings=dialog._snapshot(commit=True)
   source=dialog.source
   try:
    dialog._start_apply()
    result=_apply_once(image,source,settings,config,dialog.cache,dialog)
    applied+=1
    message=f'Applied to {source.get_name()} → {result.get_name()} ({applied})'
   except Exception as exc:
    message='Apply failed: '+str(exc)
    _notice(dialog,'Analogue Grain: apply error',str(exc)+'\n\n'+traceback.format_exc())
   finally:
    dialog._finish_apply(message)
 finally:
  if dialog is not None:
   try:dialog.cleanup()
   finally:dialog.destroy()
 return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS if applied else Gimp.PDBStatusType.CANCEL,None)

class AnalogueGrain(Gimp.PlugIn):
 def do_set_i18n(self,procedure_name):return False
 def do_query_procedures(self):return [PROC]
 def do_create_procedure(self,name):
  if name!=PROC:return None
  p=Gimp.ImageProcedure.new(self,name,Gimp.PDBProcType.PLUGIN,run,None)
  p.set_image_types('RGB*, GRAY*')
  p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
  p.set_menu_label('Analogue _Grain…');p.add_menu_path('<Image>/Filters/Artistic')
  p.set_documentation('Zone-selectable film grain','30 grain choices: five scan-measured, five sensitometry-informed, eight single-point RMS-informed, and twelve artistic interpretations. Only the five scans contain measured spatial grain. Honours feathered selections on both preview and Apply; otherwise processes the whole layer. Shows a neutral grey grain control panel at 100% or 200% with optional full-canvas preview, and lets you apply to multiple selected raster layers without closing the dialog, always creating a new layer and preserving the source.',None)
  p.set_attribution('Zarir Madon; grainifier contributors','Zarir Madon', '2026')
  help_text={
   'selection':'Main grain profile: measured scan, sensitometry-informed, RMS-informed or artistic.',
   'amount':'Global grain strength, multiplied by each tonal-zone amount.',
   'size':'Global grain scale, multiplied by each tonal-zone size.',
   'softness':'Global grain smoothing, added to tonal-zone softness.',
   'seed':'Repeatable random noise seed for the grain pattern.',
   'film_format':'Film frame width used for physical grain-size scaling.',
   'engine_version':'Saved processing model version; 4 supports grain character controls.',
   'colour_grain':'Coloured speckling strength for the colour-film interpretations.',
   'clumping':'Local grain clustering. 50 retains the emulation default.',
   'fine_balance':'Fine versus coarse grain spectral balance; 50 retains the emulation default.',
   'shadow_crossover':'Brightness percentage where the shadow grain mask has 50% weight. Higher values extend grain into brighter tones.',
   'highlight_crossover':'Brightness percentage where the highlight grain mask has 50% weight. Lower values extend grain into darker colours such as blue sky.'}
  for prefix,zone in (('shadow','shadows'),('mid','midtones'),('high','highlights')):
   help_text[prefix+'_selection']='Grain profile for '+zone+'; -1 inherits the main profile.'
   help_text[prefix+'_amount']='Grain strength for '+zone+'.'
   help_text[prefix+'_size']='Grain size multiplier for '+zone+'.'
   help_text[prefix+'_softness']='Additional grain softness for '+zone+'.'
   help_text[prefix+'_colour_grain']='Independent colour-grain strength for '+zone+' (0-100, linear). Requires a colour film.'
   help_text[prefix+'_clumping']='Independent clustering strength for '+zone+'.'
   help_text[prefix+'_fine_balance']='Independent fine/coarse balance for '+zone+'.'
  for k,v in DEFAULT.items():
   prop=k.replace('_','-')
   if k.endswith('selection'):lo=-1 if k!='selection' else 0;hi=len(LABELS)-1
   elif k=='film_format':lo=0;hi=len(FILM_FORMATS)-1
   elif k=='engine_version':lo=3;hi=4
   elif k=='seed':lo=0;hi=2147483647
   else:lo,hi,_=FLOAT_RANGES[k]
   if isinstance(v,float):p.add_double_argument(prop,k.replace('_',' ').title(),help_text[k],float(lo),float(hi),float(v),GObject.ParamFlags.READWRITE)
   else:p.add_int_argument(prop,k.replace('_',' ').title(),help_text[k],int(lo),int(hi),int(v),GObject.ParamFlags.READWRITE)
  return p

if __name__=='__main__':Gimp.main(AnalogueGrain.__gtype__,sys.argv)
