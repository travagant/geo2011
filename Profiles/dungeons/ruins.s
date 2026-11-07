// Dungeon level 2

id	"ruins";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon\\arena_back_dungeon_02_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
unit	"neutrals_masterthug"					;
unit	"neutrals_thug"					;
	healer		"empire"	1-5	5	;
	caster		"empire"	1-5	5	;
}						
						
party						
{						
unit	"neutrals_werewolf"					;
}						
						
party						
{						
unit	"neutrals_darkservant"					;
unit	"neutrals_darkservant"					;
}						
						
party						
{						
unit	"neutrals_wolf"					;
unit	"neutrals_wolf"					;
}						
						
party						
{						
unit	"neutrals_wolf"					;
unit	"neutrals_primitivegiant"					;
}						
						
party						
{						
unit	"neutrals_primitivegiant"					;
unit	"neutrals_thug"					;
unit	"elves_hunter"					;
}						
						
party						
{						
unit	"undead_SkeletonWarrior"					;
unit	"undead_Necromancer"					;
}						
						
party						
{						
unit	"undead_Vampire"					;
}						
						
party						
{						
	healer		"elves"	1-5	5	;
unit	"elves_ent"					;
unit	"elves_ent"					;
}						
						
party						
{						
unit	"demons_moloch"					;
unit	"demons_devil"					;
unit	"demons_devil"					;
}						
						
party						
{						
unit	"demons_fiend"					;
unit	"demons_demonologist"					;
}						
						
party						
{						
unit	"neutrals_spearman"					;
unit	"empire_imperialpriestess"					;
}						
						
party						
{						
unit	"neutrals_dwarf"					;
unit	"neutrals_polarbear"					;
}						
						
party						
{						
unit	"neutrals_dwarf"					;
unit	"neutrals_wolfsnow"					;
unit	"neutrals_wolfsnow"					;
}						
						
party						
{						
unit	"elves_watchman"					;
unit	"elves_watchman"					;
unit	"elves_centaurcharger"					;
}						
						
party						
{						
unit	"neutrals_goblinslinger"					;
unit	"neutrals_goblin"				5	;
unit	"neutrals_goblin"				5	;
}						
						
party						
{						
unit	"neutrals_orc"					;
unit	"neutrals_goblinarcher"					;
unit	"neutrals_goblinarcher"					;
}						
						
party						
{						
unit	"empire_titan"					;
}						
						
party						
{						
unit	"undead_Shade"					;
unit	"undead_Ghost"					;
unit	"undead_Ghost"					;
}						

rewards "dungeon_lvl_2";
