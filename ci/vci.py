import numpy as np
from vstr import utils
from vstr.cpp_wrappers.vhci_jf.vhci_jf_functions import WaveFunction, FConst, HOFunc # classes from JF's code
from vstr.cpp_wrappers.vhci_jf.vhci_jf_functions import HeatBath_Sort_FC, DoPT2FromVSCF, DoSPT2FromVSCF, VCIHamFromVSCF, VCISparseHamFromVSCF, AddStatesHBWithMax
from vstr.utils.perf_utils import TIMER
from functools import reduce
import itertools
import math
from scipy import sparse

def ReadBasisFromFile(mVHCI, FileName):
    mVHCI.Basis = []
    with open(FileName) as CHKFile:
        for Line in CHKFile:
            B = Line.split()
            B = [int(b) for b in B]
            mVHCI.Basis.append(WaveFunction(B, mVHCI.Frequencies))

def SaveBasisToFile(mVHCI, FileName):
    CHKFile = open(FileName, 'w')
    for B in mVHCI.Basis:
        Line = ""
        for i in range(mVHCI.NModes):
            Line += str(B.Modes[i].Quanta) + " "
        CHKFile.write(Line[:-1])
        CHKFile.write('\n')
    CHKFile.close()

def FormW(mVHCI, V):
    Ws = []
    for v in V:
        W = FConst(v[0], v[1], True);
        Ws.append(W)
    return Ws

def FormWSD(mVHCI):
    WSD = [[], []]
    W3 = mVHCI.Potential[0]
    W4 = mVHCI.Potential[1]
    for i in range(mVHCI.NModes):
        Wi = 0.0
        for W in W3:
            # Two cases, Wiii and Wijj
            if W.QIndices.count(i) == 1:
                Wi += 2.0 * W.fc
            elif W.QIndices.count(i) == 3:
                Wi += 3.0 * W.fc
        if abs(Wi) > 1e-12:
            mW = FConst(Wi, [i], False)
            WSD[0].append(mW)
    for i in range(mVHCI.NModes):
        for j in range(i, mVHCI.NModes):
            Wij = 0.0
            for W in W4:
            # Four cases, Wiiii, Wiikk, Wijjj, Wijkk
                if W.QIndices.count(i) == 1 and W.QIndices.count(j) == 1 and i != j:
                    Wij += 2.0 * W.fc
                elif W.QIndices.count(i) == 2 and len(W.QUnique) == 2 and i == j:
                    Wij += 2.0 * W.fc
                elif (W.QIndices.count(i) == 1 and W.QIndices.count(j) == 3) or (W.QIndices.count(i) == 3 and W.QIndices.count(j) == 1):
                    Wij += 3.0 * W.fc
                elif W.QIndices.count(i) == 4 and i == j:
                    Wij += 4.0 * W.fc
            if abs(Wij) > 1e-12:
                mW = FConst(Wij, [i, j], False)
                WSD[1].append(mW)
    mVHCI.PotentialSD = WSD

def ScreenBasis(mVHCI, Ws = None, C = None, eps = 0.01):
    if Ws is None:
        Ws = mVHCI.PotentialListFull
    if C is None:
        C = mVHCI.C[0]
    UniqueBasis, mVHCI.HighestQuanta = AddStatesHBWithMax(mVHCI.Basis, Ws, C, eps, mVHCI.MaxQuanta, mVHCI.HighestQuanta)
    print(mVHCI.HighestQuanta)
    return UniqueBasis, len(UniqueBasis)

def HCIStep(mVHCI, eps = 0.01):
    mVHCI.Timer.start(2)
    NewBasis, NAdded = mVHCI.ScreenBasis(Ws = mVHCI.PotentialListFull, C = abs(mVHCI.C[:, :mVHCI.NStates]).max(axis = 1), eps = eps)
    mVHCI.Basis += NewBasis
    mVHCI.Timer.stop(2)
    return NAdded, NewBasis

def HCI(mVHCI):
    NAdded = len(mVHCI.Basis)
    it = 1
    while (float(NAdded) / float(len(mVHCI.Basis))) > mVHCI.tol:
        NAdded, mVHCI.NewBasis = mVHCI.HCIStep(eps = mVHCI.eps1)
        print("VHCI Iteration", it, "complete with", NAdded, "new configurations and a total of", len(mVHCI.Basis), flush = True)
        mVHCI.SparseDiagonalize()
        it += 1
        if it > mVHCI.MaxIter:
            raise RuntimeError("VHCI did not converge.")
    mVHCI.NewBasis = None
    mVHCI.H = None

def PT2(mVHCI, doStochastic = False):
    assert(mVHCI.eps2 < mVHCI.eps1)
    if doStochastic:
        if mVHCI.eps3 < 0:
            mVHCI.Timer.start(4)
            mVHCI.dE_PT2, mVHCI.sE_PT2 = DoSPT2FromVSCF(mVHCI.C, mVHCI.E, mVHCI.Basis, mVHCI.PotentialListFull, mVHCI.PotentialList, mVHCI.HighestQuanta, mVHCI.eps2, mVHCI.NStates, mVHCI.NWalkers, mVHCI.NSamples, mVHCI.ModalCs, mVHCI.GenericV, False, mVHCI.eps3)
            mVHCI.Timer.stop(4)
        else:
            mVHCI.Timer.start(5)
            assert (mVHCI.eps3 < mVHCI.eps2)
            mVHCI.dE_PT2, mVHCI.sE_PT2 = DoSPT2FromVSCF(mVHCI.C, mVHCI.E, mVHCI.Basis, mVHCI.PotentialListFull, mVHCI.PotentialList, mVHCI.HighestQuanta, mVHCI.eps2, mVHCI.NStates, mVHCI.NWalkers, mVHCI.NSamples, mVHCI.ModalCs, mVHCI.GenericV, True, mVHCI.eps3)
            mVHCI.Timer.stop(5)
    else:
        mVHCI.Timer.start(3)
        mVHCI.dE_PT2 = DoPT2FromVSCF(mVHCI.C, mVHCI.E, mVHCI.Basis, mVHCI.PotentialListFull, mVHCI.PotentialList, mVHCI.HighestQuanta, mVHCI.eps2, mVHCI.NStates, mVHCI.ModalCs, mVHCI.GenericV)
        mVHCI.Timer.stop(3)
    mVHCI.E_HCI_PT2 = mVHCI.E_HCI + mVHCI.dE_PT2

def Diagonalize(mVHCI):
    mVHCI.Timer.start(1)
    H = VCIHamFromVSCF(mVHCI.Basis, mVHCI.Frequencies, mVHCI.PotentialList, mVHCI.ModalCs, mVHCI.GenericV)
    mVHCI.Timer.stop(1)
    mVHCI.Timer.start(0)
    mVHCI.E, mVHCI.C = np.linalg.eigh(H)
    mVHCI.Timer.stop(0)
    mVHCI.E_HCI = mVHCI.E[:mVHCI.NStates].copy()

def SparseDiagonalize(mVHCI):
    if mVHCI.H is None:
        mVHCI.H = VCISparseHamFromVSCF(mVHCI.Basis, mVHCI.Basis, mVHCI.Frequencies, mVHCI.PotentialList, mVHCI.ModalCs, mVHCI.GenericV, True)
    else:
        if len(mVHCI.NewBasis) != 0:
            HIJ = VCISparseHamFromVSCF(mVHCI.Basis[:-len(mVHCI.NewBasis)], mVHCI.NewBasis, mVHCI.Frequencies, mVHCI.PotentialList, mVHCI.ModalCs, mVHCI.GenericV, False)
            HJJ = VCISparseHamFromVSCF(mVHCI.NewBasis, mVHCI.NewBasis, mVHCI.Frequencies, mVHCI.PotentialList, mVHCI.ModalCs, mVHCI.GenericV, True)
            mVHCI.H = sparse.hstack([mVHCI.H, HIJ])
            mVHCI.H = sparse.vstack([mVHCI.H, sparse.hstack([HIJ.transpose(), HJJ])])
    mVHCI.Timer.stop(1)
    mVHCI.Timer.start(0)
    mVHCI.E, mVHCI.C = sparse.linalg.eigsh(mVHCI.H, k = mVHCI.NStates, which = 'SM')
    mVHCI.Timer.stop(0)
    mVHCI.E_HCI = mVHCI.E[:mVHCI.NStates].copy()

def InitTruncatedBasis(mVHCI, MaxQuanta, MaxTotalQuanta = None):
    Basis = []
    Bs = []
    B0 = [0] * mVHCI.NModes
    Bs.append(B0)
    Basis.append(B0)
    for m in range(MaxTotalQuanta):
        BNext = []
        for B in Bs:
            for i in range(len(B)):
                NewB = B.copy()
                NewB[i] += 1
                if (NewB[i] < MaxQuanta[i]):
                    if NewB not in BNext and NewB not in Basis:
                        BNext.append(NewB)
        Basis = Basis + BNext
        Bs = BNext.copy()
    
    print("Initial basis functions are:\n", Basis)
    
    # We need to translate this into the wavefunction object
    BasisWF = []
    for B in Basis:
        WF = WaveFunction(B, mVHCI.Frequencies)
        BasisWF.append(WF)
    return BasisWF

def TranslateBasisToString(B):
    BString = ""
    for j, HO in enumerate(B.Modes):
        if HO.Quanta == 1:
            BString += 'w%d + ' % (j + 1)
        elif HO.Quanta > 1:
            BString += '%dw%d + ' % (HO.Quanta, j + 1)
    return BString[:-3]

'''
This function prints the linear combination of the most important product states
'''
def LCLine(mVHCI, n, thr = 1e-2):
    def BasisToString(B):
        BString = '|'
        for HO in B.Modes:
            BString += str(HO.Quanta)
        BString += '>'
        return BString
    
    LC = ''
    for i in range(mVHCI.C.shape[0]):
        if abs(mVHCI.C[i, n]) > thr:
            LC += str(mVHCI.C[i, n])
            LC += BasisToString(mVHCI.Basis[i])
            LC += ' + '
    return LC[:-3]
    

def PrintResults(mVHCI):
    if mVHCI.dE_PT2 is None:
        FinalE = mVHCI.E_HCI
    else:
        FinalE = mVHCI.E_HCI_PT2
    for n in range(FinalE.shape[0]):
        MaxBasis = np.argmax(abs(mVHCI.C[:, n]))
        BString = TranslateBasisToString(mVHCI.Basis[MaxBasis])
        Outline = '{:.8f}\t'.format(FinalE[n])
        if mVHCI.sE_PT2 is not None:
            Outline += '+/- {:.8E}\t'.format(mVHCI.sE_PT2[n])
        Outline += '\t%s' % (BString)
        LCString = mVHCI.LCLine(n)
        Outline += '\t%s' % (LCString)
        print(Outline, flush = True)
            
'''
Class that handles VHCI
'''
class VCI:
    FormW = FormW
    FormWSD = FormWSD
    HCI = HCI
    Diagonalize = Diagonalize
    SparseDiagonalize = SparseDiagonalize
    HCIStep = HCIStep
    ScreenBasis = ScreenBasis
    PT2 = PT2
    InitTruncatedBasis = InitTruncatedBasis
    PrintResults = PrintResults
    LCLine = LCLine
    SaveBasisToFile = SaveBasisToFile
    ReadBasisFromFile = ReadBasisFromFile

    def __init__(self, mVSCF, MaxTotalQuanta = 2, NStates = 10, **kwargs):
        self.mVSCF = mVSCF
        self.ModalCs = mVSCF.Cs
        self.Frequencies = mVSCF.Frequencies # 1D array of all harmonic frequencies.
        self.NModes = mVSCF.Frequencies.shape[0]
        self.MaxQuanta = mVSCF.MaxQuanta
        self.Potential = mVSCF.Potential
        self.FormWSD()

        self.PotentialListFull = []
        for Wp in self.PotentialSD:
            self.PotentialListFull += Wp
        self.PotentialList = []
        for Wp in self.Potential:
            self.PotentialList += Wp
            self.PotentialListFull += Wp
        self.PotentialListFull = HeatBath_Sort_FC(self.PotentialListFull) # Only need to sort these once
        self.GenericV = mVSCF.AnharmTensor

        self.MaxTotalQuanta = MaxTotalQuanta
        self.HighestQuanta = [MaxTotalQuanta] * self.NModes
        for Nm in self.MaxQuanta:
            assert(Nm >= self.MaxTotalQuanta)
        self.Basis = utils.init_funcs.InitTruncatedBasis(self.NModes, self.Frequencies, self.MaxQuanta, MaxTotalQuanta = self.MaxTotalQuanta)
        self.H = None
        self.eps1 = 0.1 # HB epsilon
        self.eps2 = 0.01 # PT2/SPT2 epsilon
        self.eps3 = -1.0 # SSPT2 epsilon, < 0 means do not do semi-stochastic
        self.tol = 0.01
        self.MaxIter = 1000
        self.NStates = NStates
        self.NWalkers = 200
        self.NSamples = 50
        self.dE_PT2 = None
        self.sE_PT2 = None

        self.CHKFile = None
        self.ReadFromFile = False
        self.SaveToFile = False

        self.__dict__.update(kwargs)

        self.Timer = TIMER(6)
        self.TimerNames = ['Diagonalize', 'Form Hamiltonian', 'Screen Basis', 'PT2 correction', 'SPT2 correction', 'SSPT2 correction']

    def kernel(self, doVHCI = True, doPT2 = False, doSPT2 = False, ComparePT2 = False):

        if self.SaveToFile:
            assert(self.CHKFile is not None)
        if self.ReadFromFile:
            self.ReadBasisFromFile(self.CHKFile)

        self.Timer.start(0)
        self.Diagonalize()
        print("===== VSCF-VCI RESULTS =====", flush = True)
        self.PrintResults()
        print("")
        self.Timer.stop(0)
        
        if doVHCI:
            print("===== VSCF-VHCI RESULTS =====", flush = True)
            self.HCI()
            self.PrintResults()
            print("")
            if self.SaveToFile:
                self.SaveBasisToFile(self.CHKFile)
        if doPT2 or doSPT2:
            print("===== VSCF-VHCI+PT2 RESULTS =====", flush = True)
            self.PT2(doStochastic = doSPT2)
            self.PrintResults()
            print("")
            if ComparePT2:
                print("===== VSCF-VHCI+SPT2 RESULTS =====", flush = True)
                self.PT2(doStochastic = True)
                self.PrintResults()
                print("")
                print("===== VSCF-VHCI+SSPT2 RESULTS =====", flush = True)
                eps = self.eps2
                self.eps2 *= 10
                self.eps3 = eps
                self.PT2(doStochastic = True)
                self.PrintResults()
                print("")
        self.Timer.report(self.TimerNames)

if __name__ == "__main__":
    '''
    V2 = np.asarray([[0, 1], [1, 0]])
    Ds = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2], [2, 0], [2, 1], [2, 2]]
    w = np.asarray([1, 2])

    mVHCI = VHCI(w, [V2], MaxQuanta = 3)
    print(mVHCI.Es)
    mVHCI.HCI()
    print(mVHCI.Es)
    mVHCI.PT2(NStates = 1)
    '''

    from vstr.utils.read_jf_input import Read
    from vstr.mf.vscf import VSCF
    from vstr.vhci.vhci import VHCI
    #w, MaxQuanta, MaxTotalQuanta, Vs, eps1, eps2, eps3, NWalkers, NSamples, NStates = Read('../examples/jf_input/CLO2.inp')
    w, MaxQuanta, MaxTotalQuanta, Vs, eps1, eps2, eps3, NWalkers, NSamples, NStates = Read('CLO2.inp')
    mf = VSCF(w, Vs, MaxQuanta = MaxQuanta, NStates = NStates)
    #mf.SCF(DoDIIS = False)
    mVCI = VCI(mf, MaxTotalQuanta, eps1 = eps1, eps2 = eps2, eps3 = eps3, NWalkers = NWalkers, NSamples = NSamples, NStates = NStates)
    mVCI.CHKFile = "test.chk"
    mVCI.SaveToFile = True
    mVCI.kernel(doVHCI = True, doSPT2 = False, ComparePT2 = False)
    mVCI.ReadFromFile = True
    mVCI.kernel(doVHCI = False, doPT2 = True, ComparePT2 = False)
    #mVCI.PrintResults()

    '''
    mVHCI = VHCI(np.asarray(w), Vs, MaxQuanta = MaxQuanta, MaxTotalQuanta = MaxTotalQuanta, eps1 = eps1, eps2 = eps2, eps3 = eps3, NWalkers = NWalkers, NSamples = NSamples, NStates = NStates)
    mVHCI.PT2(doStochastic = True)
    #mVHCI.PT2(doStochastic = True)
    mVHCI.PrintResults()
    '''
