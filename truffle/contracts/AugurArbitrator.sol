pragma solidity ^0.4.18;

import './BalanceHolder.sol';

contract IRealitio {
    function notifyOfArbitrationRequest(bytes32 question_id, address requester, uint256 max_previous) external;
    function submitAnswerByArbitrator(bytes32 question_id, bytes32 answer, address answerer) external; 
    function questions(bytes32 question_id) view returns (bytes32, address, uint32, uint32, uint32, bool, uint256, bytes32, bytes32, uint256);
    function commitments(bytes32 commitment_id) view returns (uint32, bool, bytes32);
}

contract ICash{}

contract IMarket {
    function getWinningPayoutNumerator(uint256 _outcome) public view returns (uint256);
    function isFinalized() public view returns (bool);
    function isInvalid() public view returns (bool);
}

contract IUniverse {
    function getWinningChildUniverse() public view returns (IUniverse);
    function createYesNoMarket(uint256 _endTime, uint256 _feePerEthInWei, ICash _denominationToken, address _designatedReporterAddress, bytes32 _topic, string _description, string _extraInfo) public 
    payable returns (IMarket _newMarket); 
}

contract Arbitrator is BalanceHolder {

    IRealitio public realitio;
    uint256 public template_id;
    uint256 dispute_fee;

    ICash public market_token;
    mapping(address=>bool) winning_universes;
    IUniverse latest_universe;

	struct Question {
        bytes32 content_hash;
        address arbitrator;
        uint32 opening_ts;
        uint32 timeout;
        uint32 finalize_ts;
        bool is_pending_arbitration;
        uint256 bounty;
        bytes32 best_answer;
        bytes32 history_hash;
        uint256 bond;
    }

    // Stored in a mapping indexed by commitment_id, a hash of commitment hash, question, bond.
    struct Commitment {
        uint32 reveal_ts;
        bool is_revealed;
        bytes32 revealed_answer;
    }

    event LogRequestArbitration(
        bytes32 indexed question_id,
        uint256 fee_paid,
        address requester,
        uint256 remaining
    );

    struct RealitioQuestion {
        uint256 bounty;
        address disputer;
    }

    struct AugurMarket {
        bytes32 question_id; // The question the market answers
        address owner; // The address that created the market and should be paid if it resolves the question
    }

    mapping(bytes32 => RealitioQuestion) realitio_questions;
    mapping(address => AugurMarket) augur_markets;

    function initialize(IRealitio _realitio, uint256 _template_id, uint256 _dispute_fee, IUniverse _genesis_universe, ICash _market_token) {

        require(dispute_fee == 0); // uninitialized
        require(_dispute_fee > 0);
        dispute_fee = _dispute_fee;

        template_id = _template_id;
        realitio = _realitio;
        winning_universes[_genesis_universe] = true;
        latest_universe = _genesis_universe;
        market_token = _market_token;
    }

    /// @notice Register a winning child universe after a fork
    /// @dev Anyone can create Augur universes but the "correct" ones should be in a single line from the official genesis universe
    function addForkedUniverse(address parent) {
        require(winning_universes[parent]);
        IUniverse child_universe = IUniverse(parent).getWinningChildUniverse();
        winning_universes[address(child_universe)] = true;
        latest_universe = child_universe;
    }

    function createMarket( bytes32 question_id, string question, uint32 timeout, uint32 opening_ts, uint256 nonce, address asker, address designated_reporter ) {
        bytes32 content_hash = keccak256(abi.encodePacked(template_id, opening_ts, question));
        require(question_id == keccak256(abi.encodePacked(content_hash, this, timeout, asker, nonce)));

        // Create a market that's already finished
        IMarket market = latest_universe.createYesNoMarket( now, 0, market_token, designated_reporter, 0x0, question, "");
        
        augur_markets[market].question_id = question_id;
        augur_markets[market].owner = msg.sender;
    }

    /// @notice Given the last history entry, get whether they had a valid answer, the answer, and the answerer
    /// @dev These just need to be fetched from Realitio, but they can't be fetched directly because we don't store them to save gas
	/// @dev To get the final answer, we need to reconstruct the final answer using the history hash
	/// @dev TODO: This should probably be in a library offered by Realitio
    function _verifiedAnswerData(bytes32 question_id, bytes32 last_history_hash, bytes32 last_answer_or_commitment_id, uint256 last_bond, address last_answerer, bool is_commitment) 
    internal view returns (bool, bytes32)
    {
        (
            bytes32 content_hash,
            address arbitrator,
            uint32 opening_ts,
            uint32 timeout,
            uint32 finalize_ts,
            bool is_pending_arbitration,
            uint256 bounty,
            bytes32 best_answer,
            bytes32 history_hash,
            uint256 bond
        ) = realitio.questions(question_id);

        require(history_hash == keccak256(abi.encodePacked(last_history_hash, last_answer_or_commitment_id, last_bond, last_answerer, is_commitment)));
        require(is_pending_arbitration);

        bytes32 last_answer;
        bool is_answered = true;

		if (is_commitment) {
            (uint32 reveal_ts, bool is_revealed, bytes32 revealed_answer) = realitio.commitments(last_answer_or_commitment_id);
            if (is_revealed) {
                last_answer = revealed_answer;
            } else {
                is_answered = false;
            }
		} else {
            last_answer = last_answer_or_commitment_id;
        }

        return (is_answered, last_answer);

    }

    function reportAnswer(IMarket market, bytes32 last_history_hash, bytes32 last_answer_or_commitment_id, uint256 last_bond, address last_answerer, bool is_commitment) 
    public
    {

        bytes32 question_id = augur_markets[market].question_id;
        require(question_id != bytes32(0));

        // There must be an open bounty
        require(realitio_questions[question_id].bounty > 0);

        bool is_answered; // the answer was provided, not just left as an unrevealed commit
        bytes32 last_answer;
        (is_answered, last_answer) = _verifiedAnswerData(question_id, last_history_hash, last_answer_or_commitment_id, last_bond, last_answerer, is_commitment);  

        require(market.isFinalized());

        bytes32 answer;

        if (market.isInvalid()) {
            answer = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff;
        } else {
            // TODO: See if this is really how they do it
            // also if 0 is Yes and 1 No or vice versa or what
            // sort-of speculating from https://github.com/ethereum/EIPs/issues/1161#issuecomment-401053594
            uint256 yes_val = market.getWinningPayoutNumerator(0);
            uint256 no_val = market.getWinningPayoutNumerator(1);
            if (yes_val == no_val) {
                answer = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff;
            } else {
                if (yes_val > no_val) {
                    answer = 0x0000000000000000000000000000000000000000000000000000000000000001;
                } else {
                    answer = 0x0000000000000000000000000000000000000000000000000000000000000000;
                }
            }
        }

        address winner;
        if (is_answered && last_answer == answer) {
            winner = realitio_questions[question_id].disputer;
        } else {
            winner = last_answerer;
        }

        realitio.submitAnswerByArbitrator(question_id, answer, winner);
        address owner = augur_markets[market].owner;

        balanceOf[owner] += realitio_questions[question_id].bounty;

        delete augur_markets[market];
        delete realitio_questions[question_id];

    }

    /// @notice Return the dispute fee for the specified question. 0 indicates that we won't arbitrate it.
    /// @param question_id The question in question
    /// @dev Uses a general default, but can be over-ridden on a question-by-question basis.
    function getDisputeFee(bytes32 question_id) 
    public constant returns (uint256) {
        return dispute_fee;
    }


    /// @notice Request arbitration, freezing the question until we send submitAnswerByArbitrator
    /// @dev The bounty can be paid only in part, in which case the last person to pay will be considered the payer
    /// Will trigger an error if the notification fails, eg because the question has already been finalized
    /// @param question_id The question in question
    /// @param max_previous The highest bond level we should accept (used to check the state hasn't changed)
    function requestArbitration(bytes32 question_id, uint256 max_previous) 
    external payable returns (bool) {

        uint256 arbitration_fee = getDisputeFee(question_id);
        require(arbitration_fee > 0);
        require(msg.value >= arbitration_fee);

        realitio.notifyOfArbitrationRequest(question_id, msg.sender, max_previous);

        realitio_questions[question_id].bounty = msg.value;
        realitio_questions[question_id].disputer = msg.sender;

        emit LogRequestArbitration(question_id, msg.value, msg.sender, 0);

    }

}


