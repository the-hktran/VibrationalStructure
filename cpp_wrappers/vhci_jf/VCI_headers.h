/*

#############################################################################
#                                                                           #
#              Vibrational Heat-Bath Configuration Interaction              # 
#                         By: Jonathan H. Fetherolf                         #
#                                                                           #
#                      Based on LOVCI by Eric G. Kratz                      #
#                                                                           #
#############################################################################

Headers, libraries, and data structures for VHCI

*/

//Make including safe
#ifndef VCI_HEADERS
#define VCI_HEADERS

//Header Files
#include <omp.h>
#include <cstdlib>
#include <ctime>
#include <iostream>
#include <sstream>
#include <iomanip>
#include <numeric>
#include <string>
#include <complex>
#include <cmath>
#include <fstream>
#include <vector>
#include <map>
#include <unordered_set>
#include <algorithm>
#include <sys/stat.h>
#include <Eigen/Core>
#include <Eigen/Dense>
#include <Eigen/LU>
#include <Eigen/QR>
#include <Eigen/SVD>
#include <Eigen/Eigenvalues>
#include <Eigen/StdList>
#include <Eigen/Eigen>
#include <Eigen/StdVector>
#include <Eigen/Sparse>
#include <Spectra/SymEigsSolver.h>
#include <Spectra/MatOp/SparseSymMatProd.h>
#include <boost/functional/hash.hpp>
#include <numeric>

//Set namespaces for common libraries
using namespace Eigen;
using namespace std;
using namespace Spectra;
using namespace boost;


//Global exact constants
const double pi = 4*atan(1); //Pi
const double rt2pi = sqrt(2*pi); //Needed for Gaussian broadening

//Global measured constants (NIST, CODATA 2010)
const double cs = 2.99792458e-10; //Speed of light (cm)
const double k = 0.69503476; //Boltzmann constant (cm^-1)

//Global derived constants
const double h = 2*pi; //Planck constant (cm^-1)

//Custom data structures
struct HOFunc
{
    //Data structure for harmonic oscillator basis functions
    double Freq; //Frequency
    int Quanta; //Number of quanta in the mode
    // condition for equality
    bool operator == (const HOFunc &other) const {
        return (Freq == other.Freq) && (Quanta == other.Quanta);
    }
    // condition for inequality
    bool operator != (const HOFunc &other) const {
        return (Freq != other.Freq) || (Quanta != other.Quanta);
    }
};

class WaveFunction
{
    public:
    //Data structure for storing a VCI wavefunction
    int M; //Number of modes
    vector<HOFunc> Modes; // WaveFunctions
    WaveFunction(std::vector<int> Quantas, std::vector<double> Frequencies);
    bool operator == (const WaveFunction &other) const {
        bool same_wfn = 1;
        if(other.M != M){
            return 0;
        }
        for(int n=0; n<other.M; n++){
            if(other.Modes[n] != Modes[n]){
                return 0;
            }
        }
        return same_wfn;
    }
    // condition for inequality
    bool operator != (const WaveFunction &other) const {
        bool diff_wfn = 0;
        if(other.M != M){
            return 1;
        }
        for(int n=0; n<other.M; n++){
            if(other.Modes[n] != Modes[n]){
                return 1;
            }
        }
        return diff_wfn;
    }

};

WaveFunction::WaveFunction(std::vector<int> Quantas, std::vector<double> Frequencies)
{
    for (unsigned int i = 0; i < Quantas.size(); i++)
    {
        HOFunc myHO;
        myHO.Freq = Frequencies[i];
        myHO.Quanta = Quantas[i];
        Modes.push_back(myHO);
    }
}

class FConst
{
    public:
    //Data structure for anharmonic force constants
    //Note: fc should include the permutation term
    int Order; // Order of the FC
    double fc; //Value of the force constant
    vector<int> fcpow;
    vector<int> QUnique; // Shortened list of modes only including those affected
    vector<int> QPowers; // Power for each affected mode
    std::vector<int> QIndices;

    FConst(double dV, std::vector<int> Qs, bool doScale);
    void ScaleW();
};

FConst::FConst(double dV, std::vector<int> Qs, bool doScale)
{
    fc = dV;
    QIndices = Qs;
    Order = QIndices.size();
    QUnique = Qs;
    std::sort(QUnique.begin(), QUnique.end());
    auto Last = std::unique(QUnique.begin(), QUnique.end());
    QUnique.erase(Last, QUnique.end());
    for (int i : QUnique) QPowers.push_back(std::count(QIndices.begin(), QIndices.end(), i));
    for (unsigned int i = 0; i < Order; i++) fcpow.push_back(std::count(QIndices.begin(), QIndices.end(), i));
    if (doScale) ScaleW();
}

double Factorial(int n)
{
    double nFac = 1.0;
    for (int i = 0; i < n; i++)
    {
        nFac *= (double)(i + 1);
    }
    return nFac;
}

void FConst::ScaleW()
{
    fc = fc / sqrt(pow(2.0, Order));
    for (int i = 0; i < QPowers.size(); i++)
    {
        fc /= Factorial(QPowers[i]);
    }
}


struct WfnHasher
{
    size_t operator () (const WaveFunction& key) const 
    {
        // function to generate unique hash for a WaveFunction using Boost
        size_t seed = 0;
        for(int n=0; n<key.M; n++){
            hash_combine(seed, hash_value(key.Modes[n].Quanta));
            //hash_combine(seed, key.Modes[n].Freq);
        }
        return seed;
    }
};

typedef unordered_set<WaveFunction, WfnHasher> HashedStates;
typedef SparseMatrix<double, 0, ptrdiff_t> SpMat;
typedef Triplet<double, ptrdiff_t> Trip;

inline void CreationLO(double& ci, int& ni)
{
    //Creation ladder operator
    ci *= sqrt(ni+1); //Update coefficient
    ni += 1; //Update state
    return;
};

inline void AnnihilationLO(double& ci, int& ni)
{
    //Annihilation ladder operator
    ci *= sqrt(ni); //Update coefficient
    ni -= 1; //Update state
    //Check for impossible states
    if (ni < 0)
    {
        ni = -1; //Used later to remove the state
        ci = 0; //Delete state
    }
    return;
};

inline void QDiffVec(WaveFunction &Bn, WaveFunction &Bm, int &qtot, int &mchange, vector<int> &DiffVec)
{
    for (unsigned int i = 0; i < Bn.M; i++)
    {
        int qdiff = 0;
        qdiff += Bn.Modes[i].Quanta;
        qdiff -= Bm.Modes[i].Quanta;
        DiffVec[i] = abs(qdiff);
        qtot += abs(qdiff);
        if (qdiff != 0) mchange += 1;
    }
    return;
}

inline bool ScreenState(int qdiff, int mchange, const vector<int>& QDiffVec, const FConst& fc)
{
    //Function for ignoring states that have no overlap
    //qdiff is the number of changed quanta, mchange is number modes with changed quanta, QDiffVec is a vector with number of changed quanta per mode, fc is force constant
    bool keepstate = 1; //Assume the state is good
    if(qdiff > fc.QPowers.size() || 
            mchange > fc.QPowers.size()
            || qdiff%2 != fc.QPowers.size()%2
            ){
        return 0;
    }
    //Check based on force constant powers (check that raising and lowering results in quanta match)
    for (unsigned int i=0;i<fc.QPowers.size();i++)
    {
        if ( QDiffVec[fc.QPowers[i]] > fc.QPowers[i] || 
                QDiffVec[fc.QPowers[i]] % 2 != fc.QPowers[i] % 2){
            //Impossible for the states to overlap if mode power is too small or wrong even/odd parity
            //Skip the rest of the checks
            return 0;
        }
    }
    //Check overlap of all other modes (modes not involved in FC)
    for (int i=0;i<QDiffVec.size();i++)
    {
        bool cont = 1; //Continue the check
        for (unsigned int j=0;j<fc.QPowers.size();j++)
        {
            if (fc.QPowers[j] == i)
            {
                //Ignore this mode since it is in the FC
                cont = 0;
                break; // No need to continue checking mode against fc.QPowers
            }
        }
        if (cont)
        {
            if ( QDiffVec[i] != 0)
            {
                //Remove state due to zero overlap in mode i
                return 0; // No need to check if the other modes match 
            }
        }
    }
    //Return decision
    return keepstate;
};

//Function declarations
std::tuple<Eigen::VectorXd, Eigen::MatrixXd> DenseDiagonalizeCPP(std::vector<WaveFunction> &BasisSet, std::vector<double> &Frequencies, std::vector<FConst> &AnharmPot, std::vector<FConst> &CubicFC, std::vector<FConst> &QuarticFC, std::vector<FConst> &QuinticFC, std::vector<FConst> &SexticFC);
std::tuple<Eigen::VectorXd, Eigen::MatrixXd> SparseDiagonalizeCPP(std::vector<WaveFunction> &BasisSet, std::vector<double> &Frequencies, std::vector<FConst> &AnharmPot, std::vector<FConst> &CubicFC, std::vector<FConst> &QuarticFC, std::vector<FConst> &QuinticFC, std::vector<FConst> &SexticFC, int NEig);

std::vector<WaveFunction> AddStatesHB(std::vector<WaveFunction> &BasisSet, std::vector<FConst> &AnharmHB, Eigen::VectorXd &C, double eps);
void HeatBath_Sort_FC(std::vector<FConst> &AnharmHB);

std::vector<double> DoPT2(MatrixXd& Evecs, VectorXd& Evals, std::vector<WaveFunction> &BasisSet, std::vector<FConst> &AnharmHB, std::vector<FConst> &AnharmFC, std::vector<FConst> &CubicFC, std::vector<FConst> &QuarticFC, std::vector<FConst> &QuinticFC, std::vector<FConst> &SexticFC, double PT2_Eps, int NEig);
std::tuple<std::vector<double>, std::vector<double>> DoSPT2(MatrixXd& Evecs, VectorXd& Evals, std::vector<WaveFunction> &BasisSet, std::vector<WaveFunction> &PTBasisSet, std::vector<FConst> &AnharmFC, std::vector<FConst> &CubicFC, std::vector<FConst> &QuarticFC, std::vector<FConst> &QuinticFC, std::vector<FConst> &SexticFC, double PT2_Eps, int NEig, int Nd, int Ns, bool SemiStochastic, double PT2_Eps2);

//Function definitions 
#include "Ham.cpp"
#include "HB.cpp"
#include "PT2.cpp"

#endif
