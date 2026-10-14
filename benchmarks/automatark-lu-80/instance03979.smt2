(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (re.union (str.to_re "GIR") (re.++ (re.range "A" "Z") (re.range "0" "9") (re.opt (re.union (re.range "A" "Z") (re.range "0" "9")))) (re.++ ((_ re.loop 2 2) (re.range "A" "Z")) (re.range "0" "9") (re.opt (re.union (re.range "A" "Z") (re.range "0" "9"))))) (re.opt (str.to_re " ")) (str.to_re "\u{a}") (re.range "0" "9") ((_ re.loop 2 2) (re.range "A" "Z"))))))
(check-sat)

(exit)
