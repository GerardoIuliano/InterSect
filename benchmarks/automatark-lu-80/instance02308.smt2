(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (re.union (re.++ ((_ re.loop 1 1) (re.range "1" "3")) (re.* (re.range "0" "9")) (re.opt (re.++ (str.to_re ".") ((_ re.loop 1 1) (re.range "0" "9"))))) (re.++ (str.to_re "0") (re.opt (re.++ (str.to_re ".") ((_ re.loop 1 1) (re.range "0" "9"))))) (re.++ ((_ re.loop 1 1) (str.to_re "4")) (re.* (re.range "0" "9")) (re.opt (re.++ (str.to_re ".") ((_ re.loop 1 1) (str.to_re "0"))))) (re.++ (str.to_re "5.") ((_ re.loop 1 1) (str.to_re "5")))) (str.to_re "\u{a}")))))
(check-sat)

(exit)
