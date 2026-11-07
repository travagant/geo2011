// Dungeon level 2

id	"Stone_tower";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon\\arena_back_dungeon_01_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
unit	"empire_elementalist"					;
unit	"elves_earthelemental"					;
}						
						
party						
{						
unit	"demons_marblegargoyle"					;
unit	"demons_marblegargoyle"					;
}						
						
party						
{						
unit	"neutrals_darkservant"					;
unit	"neutrals_darkservant"					;
}						
						
party						
{						
unit	"empire_wizard"					;
unit	"empire_livingarmor"					;
unit	"empire_livingarmor"					;
}						
						
party						
{						
unit	"empire_wizard"					;
unit	"empire_apprentice"				7	;
unit	"empire_apprentice"				7	;
}						
						
party						
{						
unit	"demons_demonologist"					;
unit	"demons_hellhound"					;
}						
						
party						
{						
unit	"undead_Lich"				25	;
}						
						
party						
{						
unit	"undead_Necromancer"					;
unit	"undead_Doomdrake"					;
}						
						
party						
{						
unit	"demons_beliarh"				25	;
}						
						
party						
{						
unit	"empire_whitewizard"				25	;
}						
						
party						
{						
unit	"undead_templar"					;
unit	"undead_templar"					;
unit	"undead_Wraith"					;
}						
						
party						
{						
unit	"elves_theurgist"					;
unit	"elves_hunter"					;
unit	"elves_hunter"					;
}						
						
party						
{						
unit	"elves_sentry"				25	;
}						
						
party						
{						
unit	"elves_warden"				25	;
}						
						
party						
{						
unit	"elves_griffin"					;
unit	"elves_oracle"				5	;
}						
						
party						
{						
unit	"neutrals_manatarms"					;
unit	"neutrals_manatarms"					;
unit	"empire_mage"					;
unit	"empire_apprentice"					;
unit	"empire_apprentice"					;
}						
						
party						
{						
unit	"demons_hag"					;
unit	"neutrals_cityguard"					;
}						
						
party						
{						
unit	"demons_sorcerer"					;
	melee		"legions_giant"	5-13	13	;
}						
						
party						
{						
	melee	2	"empire"	1-5	5	;
	range		"empire"	1-7	7	;
	healer		"empire"	1-5	5	;
}						
						
party						
{						
unit	"demons_antipaladin"					;
unit	"demons_hellhound"					;
}						
						
party						
{						
unit	"undead_DarkLord"					;
unit	"undead_Ghost"				5	;
}						

rewards "dungeon_lvl_2";
