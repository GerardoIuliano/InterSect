(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ ((_ re.loop 2 2) (re.union (str.to_re "5") (str.to_re "1") (str.to_re "|") (str.to_re "2") (str.to_re "3") (str.to_re "4"))) ((_ re.loop 14 14) (re.range "0" "9")) (str.to_re "\u{a}")))))
(check-sat)

(exit)
