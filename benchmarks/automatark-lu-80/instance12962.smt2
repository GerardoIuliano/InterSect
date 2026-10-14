(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (not (str.in_re X (re.++ (re.opt (str.to_re "+39")) (re.union (re.++ (str.to_re "38") (re.union (str.to_re "{") (str.to_re "8") (str.to_re ",") (str.to_re "9") (str.to_re "}") (str.to_re "|") (str.to_re "0"))) (re.++ (str.to_re "34") (re.union (str.to_re "{") (re.range "7" "9") (str.to_re "}") (str.to_re "|") (str.to_re "0"))) (re.++ (str.to_re "36") (re.union (str.to_re "6") (str.to_re "|") (str.to_re "8") (str.to_re "0"))) (re.++ (str.to_re "33") (re.union (str.to_re "{") (re.range "3" "9") (str.to_re "}") (str.to_re "|") (str.to_re "0"))) (re.++ (str.to_re "32") (re.union (str.to_re "{") (str.to_re "8") (str.to_re ",") (str.to_re "9") (str.to_re "}")))) ((_ re.loop 7 7) (re.range "0" "9")) (str.to_re "\u{a}")))))
(assert (not (str.in_re X (re.union (re.++ (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))) (re.opt (str.to_re "(")) (str.to_re "0") ((_ re.loop 4 4) (re.range "0" "9")) (re.opt (str.to_re ")")) (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))) ((_ re.loop 6 6) (re.range "0" "9")) (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}")))) (re.++ (str.to_re "\u{a}") (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))) (re.opt (str.to_re "(")) (str.to_re "0") ((_ re.loop 3 3) (re.range "0" "9")) (re.opt (str.to_re ")")) (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))) ((_ re.loop 3 3) (re.range "0" "9")) (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))) ((_ re.loop 4 4) (re.range "0" "9")) (re.* (re.union (str.to_re " ") (str.to_re "\u{9}") (str.to_re "\u{a}") (str.to_re "\u{c}") (str.to_re "\u{d}"))))))))
(check-sat)

(exit)
