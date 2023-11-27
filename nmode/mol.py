import numpy as np
import scipy
import numdifftools as nd
from vstr.utils import init_funcs, constants
from vstr.ff.force_field import ScaleFC_me
A2B = 1.88973
AU2CM = 219474.63 
AMU2AU = 1822.888486209

def ho_3d(x):
    w = [0.00751348, 0.01746552, 0.01797136]
    #w = [1642.62705755, 3825.7245683,  3929.27450625]
    return (0.5 * (w[0]**2 * x[0, 0]**2 + w[1]**2 * x[0, 1]**2 + w[2]**2 * x[0, 2]**2) * 1836.15267389
            + 0.25 * 100 * x[0, 1] * x[0, 1] * x[0, 2] * x[0, 2]) # + 0.001 * x[0, 0] * x[0, 1] * x[0, 2] + 0.01 * x[0, 0]**2 * x[0, 1]**2 * x[0, 2]**2)

class Molecule():
    '''
    Atomic units are used throughout. 
    '''

    def __init__(self, potential_cart, natoms, mass, **kwargs):

        self.potential_cart = potential_cart
        self.natoms = natoms
        self.Nm = 3 * natoms - 6
        self.mass = mass

        self.ngridpts = None

        self.Order = 2

        self.FullMaxQuanta = 10
        self.FullTotalQuanta = 5
        self.InitMaxQuanta = 10
        self.InitTotalQuanta = 2

        self.__dict__.update(kwargs)

        if (isinstance(self.FullMaxQuanta, (int, np.int))):
            self.FullMaxQuanta = [self.FullMaxQuanta] * self.Nm
        if (isinstance(self.InitMaxQuanta, (int, np.int))):
            self.InitMaxQuanta = [self.InitMaxQuanta] * self.Nm

    def _potential(self, x):
        """
        Calculate the potential with 3N-dimensional argument.
        """
        return self.potential_cart(x.reshape((self.natoms,3)))

    def CalcNM(self, x0 = None):
        self.nm = NormalModes(self)
        self.nm.kernel(x0 = x0)
        #debug!!
        #c = np.zeros((self.natoms * 3, self.nm.nmodes))
        #c[:3,:3] = np.eye(3)
        #self.nm.nm_coeff = c.reshape((self.natoms, 3, self.nm.nmodes))
        #print(self.nm.freqs)
        #self.potential_cart = ho_3d
        #self.nm.x0 = np.zeros_like(self.nm.x0)
        # debug!!
        self.Frequencies = self.nm.freqs * constants.AU_TO_INVCM

    def CalcNModePotential(self, Order = None):
        if Order is None:
            Order = self.Order
        self.nmode = NModePotential(self.nm)
        self.ints = [np.asarray([]), np.asarray([[[[[[]]]]]]), np.asarray([[[[[[[[[]]]]]]]]])]
        for i in range(Order):
            self.ints[i] = self.nmode.get_ints(i+1, ngridpts = self.ngridpts)
        #if Order >= 2:
        #    N = len(self.ints[0])
        #    for i in range(N):
        #        for j in range(N):
        #            self.ints[1][i,j] = self.ints[1][i,j] - self.ints[0][i] - self.ints[0][j]

    def InitializeBasis(self):
        self.FullBasis = init_funcs.InitTruncatedBasis(self.Nm, self.Frequencies, self.FullMaxQuanta, self.FullTotalQuanta)
        self.Basis = init_funcs.InitTruncatedBasis(self.Nm, self.Frequencies, self.InitMaxQuanta, self.InitTotalQuanta)

    def ReorganizeBasis(self):
        IndexBasis = []
        for B in self.Basis:
            IndexBasis.append(self.FullBasis.index(B))
        IndexOther = [i for i in range(len(self.FullBasis)) if i not in IndexBasis]
        FullBasis = [self.FullBasis[i] for i in IndexBasis] + [self.FullBasis[i] for i in IndexOther]
        self.FullBasis = FullBasis
        return FullBasis, IndexBasis, IndexOther

    def kernel(self, x0 = None):
        self.CalcNM(x0 = x0)
        self.InitializeBasis()
        self.ReorganizeBasis()
        # The NMode potential is done in HO basis, doesn't see product basis
        self.CalcNModePotential()


class NormalModes():

    def __init__(self, mol):
        self.mol = mol

        self.nmodes = 3*self.mol.natoms - 6

    def kernel(self, x0=None):
        """
        Calculate the normal modes of the potential defined in mol.
    
        Parameters:
        x0 ((natoms,3) ndarray): Guess of the minimum of the PES
        """

        natoms = self.mol.natoms

        if x0 is None:
            x0 = np.random.random((natoms,3))

        pes = self.mol._potential
        result = scipy.optimize.minimize(pes, x0)
        self.x0 = result.x.reshape((natoms,3))
        
        hess0 = self.hessian(self.x0).reshape((natoms*3, natoms*3))

        e, v = scipy.linalg.eigh(hess0)
        e, v = e[6:], v[:,6:] # remove translations and rotations
        v = v.reshape((natoms,3,self.nmodes))
        self.freqs, self.nm_coeff = np.sqrt(e), v
        
        return self.freqs, self.nm_coeff

    def gradient(self, x):
        pes = self.mol._potential
        grad = nd.Gradient(pes)(x.reshape(-1))
        return grad.reshape((self.mol.natoms,3))

    def hessian(self, x):
        """
        Calculate the mass-weighted Hessian.
        """
        natoms = self.mol.natoms
        mass = self.mol.mass
        pes = self.mol._potential
        hess = nd.Hessian(pes)(x.reshape(-1))
        hess = hess.reshape((natoms,3,natoms,3))
        for i in range(natoms):
            for j in range(natoms):
                hess[i,:,j,:] /= np.sqrt(mass[i]*mass[j])
        return hess

    def get_ff(self, dx = 1e-4, Order = 4):
        """
        Calculate the cubic and quartic derivatives of the potential with respect to normal modes
        """
        natoms = self.mol.natoms
        mass = self.mol.mass
        pes = self.mol._potential
        freq_cm = self.freqs * constants.AU_TO_INVCM
        def hess(x):
            C = np.einsum('nij,n->nij', self.nm_coeff, 1/np.sqrt(mass))
            C = C.reshape(3 * natoms, self.nmodes)
            return np.einsum('ki,kl,lj->ij', C, nd.Hessian(pes)(x.reshape(-1)), C)
        H0 = hess(self.x0)
        V3 = []
        V4 = []
        for i in range(self.nmodes):
            q = np.zeros(self.nmodes)
            q[i] = dx
            x = self._normal2cart(q)
            Hpi = hess(x)

            q = np.zeros(self.nmodes)
            q[i] = -dx
            x = self._normal2cart(q)
            Hmi = hess(x)

            d3V = (Hpi - Hmi) / (2 * dx)
            d4Vd = (Hpi + Hmi - 2 * H0) / (dx**2)
            for j in range(i, self.nmodes):
                for k in range(j, self.nmodes):
                    Vijk = ScaleFC_me(d3V[j, k], freq_cm, [i, j, k])
                    Viijk = ScaleFC_me(d4Vd[j, k], freq_cm, [i, i, j, k])
                    if abs(Vijk) > 1.0:
                        V3.append((Vijk, [i, j, k]))
                    if abs(Viijk) > 1.0:
                        V4.append((Viijk, [i, i, j, k]))
                
            for j in range(i + 1, self.nmodes):
                q = np.zeros(self.nmodes)
                q[i] = dx
                q[j] = dx
                x = self._normal2cart(q)
                Hpipj = hess(x)

                q = np.zeros(self.nmodes)
                q[i] = dx
                q[j] = -dx
                x = self._normal2cart(q)
                Hpimj = hess(x)

                q = np.zeros(self.nmodes)
                q[i] = -dx
                q[j] = dx
                x = self._normal2cart(q)
                Hmipj = hess(x)

                q = np.zeros(self.nmodes)
                q[i] = -dx
                q[j] = -dx
                x = self._normal2cart(q)
                Hmimj = hess(x)

                d4V = (Hpipj + Hmimj - Hmipj - Hpimj) / (4 * dx**2)
                for k in range(j, self.nmodes):
                    for l in range(k, self.nmodes):
                        Vijkl = ScaleFC_me(d4V[k, l], freq_cm, [i, j, k, l])
                        if abs(Vijkl) > 1.0:
                            V4.append((Vijkl, [i, j, k, l]))
        return V3, V4

    def _normal2cart(self, q):
        return self.x0 + np.einsum('n,ndi,i->nd',1/np.sqrt(self.mol.mass),self.nm_coeff,q)

    def _cart2normal(self, x):
        return np.einsum('ndi,n,nd->i', self.nm_coeff, np.sqrt(self.mol.mass), x-self.x0)

    def potential_1mode(self, i, qi):
        """
        Calculate the 1-mode potential.
        
        Parameters:
        i (int): the mode index
        qi (float): the normal mode coordinate
        """
        q = np.zeros(self.nmodes)
        q[i] = qi
        x = self._normal2cart(q)
        return self.mol.potential_cart(x)

    def potential_2mode(self, i, j, qi, qj):
        q = np.zeros(self.nmodes)
        q[i] = qi
        q[j] = qj
        x = self._normal2cart(q)
        return (self.mol.potential_cart(x)
                - self.potential_1mode(i,qi) - self.potential_1mode(j,qj))

    def potential_3mode(self, i, j, k, qi, qj, qk):
        q = np.zeros(self.nmodes)
        q[i] = qi
        q[j] = qj
        q[k] = qk
        x = self._normal2cart(q)
        return (self.mol.potential_cart(x) - self.potential_2mode(i, j, qi, qj) - self.potential_2mode(i, k, qi, qk) - self.potential_2mode(j, k, qj, qk) - self.potential_1mode(i, qi) - self.potential_1mode(j, qj) - self.potential_1mode(k, qk))

def get_qmat_ho(omega, nmax):
    qmat = np.zeros((nmax,nmax))
    for n in range(nmax-1):
        qmat[n,n+1] = qmat[n+1,n] = np.sqrt((n+1)/(2*omega))
    return qmat


def get_tmat_ho(omega, nmax):
    tmat = np.zeros((nmax,nmax))
    for n in range(nmax):
        tmat[n,n] = omega/2*(n+0.5)
        if n < nmax-2:
            tmat[n,n+2] = tmat[n+2,n] = -omega/4*np.sqrt((n+1)*(n+2))
    return tmat


class HEG1Mode():

    def __init__(self, nm):
        self.nm = nm

    def kernel(self, ngridpts=None):
        """
        Calculate the HEG grid points and functions by diagonalizing the Q
        matrix in a basis of harmonic oscillator eigenfunctions.

        Parameters:
        ngridpts (int or list of int): The number of harmonic oscillator
            eigenfunctions (per mode) to use when diagonalizing Q.
        """
        if ngridpts is None:
            ngridpts = [12]*self.nm.nmodes
        elif isinstance(ngridpts, (int, np.integer)):
            ngridpts = [ngridpts]*self.nm.nmodes 

        self.ngridpts = ngridpts
        gridpts = list()
        coeff = list()
        for i in range(self.nm.nmodes):
            nho = ngridpts[i]
            omega = self.nm.freqs[i]
            qmat = get_qmat_ho(omega, nho)
            q, u = scipy.linalg.eigh(qmat)
            gridpts.append(q)
            coeff.append(u)

        self.gridpts, self.coeff = gridpts, coeff
        return self.gridpts, self.coeff


class HEGOpt1Mode():
    def __init__(self, heg0):
        self.heg0 = heg0
        self.nm = heg0.nm

    def kernel(self, ngridpts):
        if ngridpts is None:
            ngridpts = [8]*self.nm.nmodes
        elif isinstance(ngridpts, (int, np.integer)):
            ngridpts = [ngridpts]*self.nm.nmodes 

        self.ngridpts = ngridpts

        gridpts = list()
        coeff = list()
        for i in range(self.nm.nmodes):
            nho = self.heg0.ngridpts[i]
            omega = self.nm.freqs[i]
            q0, v0 = self.heg0.gridpts[i], self.heg0.coeff[i]
            tmat = get_tmat_ho(omega, nho)
            vgrid = np.array([self.nm.potential_1mode(i,qi) for qi in q0]) # this should be vectorized
            vmat = np.dot(v0, np.dot(vgrid, v0.T))
            e, v = scipy.linalg.eigh(tmat+vmat)
            v = v[:,:ngridpts[i]]
            qmat0 = get_qmat_ho(omega, nho)
            qmat = np.dot(v.T, np.dot(qmat0, v))
            q, u = scipy.linalg.eigh(qmat)
            gridpts.append(q)
            coeff.append(u)

        self.gridpts, self.coeff = gridpts, coeff
        return self.gridpts, self.coeff
        

class NModePotential():
    def __init__(self, nm):
        self.nm = nm

    def get_ints(self, nmode, ngridpts=None, optimized=False, ngridpts0=None):
        
        if optimized is False:
            if ngridpts is None:
                ngridpts = [12]*self.nm.nmodes
            elif isinstance(ngridpts, (int, np.integer)):
                ngridpts = [ngridpts]*self.nm.nmodes

            gridpts, coeff = self.get_heg(ngridpts)

        else:
            if ngridpts is None:
                ngridpts = [8]*self.nm.nmodes
            elif isinstance(ngridpts, (int, np.integer)):
                ngridpts = [ngridpts]*self.nm.nmodes

            if ngridpts0 is None:
                ngridpts0 = [12]*self.nm.nmodes
            elif isinstance(ngridpts0, (int, np.integer)):
                ngridpts0 = [ngridpts0]*self.nm.nmodes

            gridpts, coeff = self.get_heg(ngridpts, optimized, ngridpts0)

        nmodes = self.nm.nmodes
        if nmode == 1:
            ints = np.empty(nmodes, dtype=object)
            for i in range(nmodes):
                vgrid = np.array([self.nm.potential_1mode(i,qi) for qi in gridpts[i]]) # this should be vectorized
                vi = np.dot(coeff[i], np.dot(np.diag(vgrid), coeff[i].T))
                ints[i] = vi * constants.AU_TO_INVCM
                print(ints[i])
        elif nmode == 2:
            ints = np.empty((nmodes,nmodes), dtype=object)
            for i in range(nmodes):
                for j in range(nmodes):
                    vgrid = np.array([[self.nm.potential_2mode(i,j,qi,qj) for qj in gridpts[j]] for qi in gridpts[i]]) # this should be vectorized
                    vij = np.einsum('gp,hq,gh,gr,hs->pqrs', coeff[i].T, coeff[j].T, vgrid, coeff[i].T, coeff[j].T, optimize=True)
                    ints[i, j] = vij * constants.AU_TO_INVCM
        elif nmode == 3:
            ints = np.empty((nmodes,nmodes,nmodes), dtype=object)
            for i in range(nmodes):
                for j in range(nmodes):
                    for k in range(nmodes):
                        vgrid = np.array([[[self.nm.potential_3mode(i,j,k,qi,qj,qk) for qk in gridpts[k]] for qj in gridpts[j]] for qi in gridpts[i]])
                        vijk = np.einsum('gp,hq,fr,ghf,gs,ht,fu->pqrstu', coeff[i].T, coeff[j].T, coeff[k].T, vgrid, coeff[i].T, coeff[j].T, coeff[k].T, optimize=True)
                        ints[i, j, k] = vijk * constants.AU_TO_INVCM

        return ints


    def get_heg(self, ngridpts, optimized=False, ngridpts0=None):
        """
        Calculate the HEG grid points and functions by diagonalizing the Q
        matrix in a basis of harmonic oscillator eigenfunctions.

        Parameters:
        ngridpts (int or list of int): The number of harmonic oscillator
            eigenfunctions (per mode) to use when diagonalizing Q.
        """
        self.ngridpts = ngridpts
        gridpts = list()
        coeff = list()
        for i in range(self.nm.nmodes):
            if optimized: nho = ngridpts0[i]
            else: nho = ngridpts[i]
            omega = self.nm.freqs[i]
            qmat = get_qmat_ho(omega, nho)
            q, u = scipy.linalg.eigh(qmat)

            if optimized:
                q0, u0 = q, u 

                tmat = get_tmat_ho(omega, nho)
                vgrid = np.array([self.nm.potential_1mode(i,qi) for qi in q0]) # this should be vectorized
                vmat = np.dot(u0, np.dot(np.diag(vgrid), u0.T))
                e, c = scipy.linalg.eigh(tmat+vmat)
                c = c[:,:ngridpts[i]]
                qmat = np.dot(c.T, np.dot(qmat, c))
                q, u = scipy.linalg.eigh(qmat)

            gridpts.append(q)
            coeff.append(u)

        self.gridpts, self.coeff = gridpts, coeff
        return self.gridpts, self.coeff 
